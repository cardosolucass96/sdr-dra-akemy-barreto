from __future__ import annotations

from pathlib import Path

from scripts.quality.check_diff_coverage import (
    CoveredLine,
    evaluate_diff_coverage,
    parse_cobertura_coverage,
)
from scripts.quality.check_python_structure import (
    build_import_graph,
    function_metrics,
    graph_contract_findings,
    invalid_layer_dependency,
    node_contract_findings,
    parse_sources,
    strongly_connected_components,
    structure_findings,
)
from scripts.quality.common import DiffChanges, QualityConfig, parse_unified_diff

QUALITY_CONFIG = QualityConfig(
    source_root="src/app",
    node_root="src/app/agent/nodes",
    complexity_warning=11,
    complexity_max=14,
    node_complexity_max=8,
    node_max_lines=40,
    node_max_internal_imports=4,
    diff_coverage_minimum=85.0,
)


def _parsed(source_by_path: dict[str, str]):
    return parse_sources(source_by_path, "src/app")


def test_quality_checker_keeps_its_own_functions_below_blocking_complexity() -> None:
    quality_sources = {
        f"src/app/quality/{path.name}": path.read_text()
        for path in Path("scripts/quality").glob("*.py")
    }
    metrics = [
        metric
        for parsed in _parsed(quality_sources).values()
        for metric in function_metrics(parsed)
    ]

    assert metrics
    assert max(metric.complexity for metric in metrics) <= QUALITY_CONFIG.complexity_max


def test_parse_unified_diff_tracks_added_lines_and_paths() -> None:
    diff = """diff --git a/src/app/example.py b/src/app/example.py
--- a/src/app/example.py
+++ b/src/app/example.py
@@ -2,2 +2,3 @@
+first
+second
+third
diff --git a/src/app/new.py b/src/app/new.py
--- /dev/null
+++ b/src/app/new.py
@@ -0,0 +1,2 @@
+one
+two
"""

    changes = parse_unified_diff(diff)

    assert changes.paths == {"src/app/example.py", "src/app/new.py"}
    assert changes.lines_by_path == {
        "src/app/example.py": {2, 3, 4},
        "src/app/new.py": {1, 2},
    }


def test_function_metrics_count_decisions_without_nested_function_body() -> None:
    parsed = _parsed(
        {
            "src/app/example.py": """def evaluate(value):
    if value and value > 2:
        return 1
    for item in value:
        if item:
            return 2
    def nested(flag):
        if flag:
            return 3
    return 0
"""
        }
    )["app.example"]

    metrics = {metric.name: metric for metric in function_metrics(parsed)}

    assert metrics["evaluate"].complexity == 5
    assert metrics["evaluate.nested"].complexity == 2
    assert metrics["evaluate"].effective_lines == 10


def test_import_graph_detects_cycles() -> None:
    parsed = _parsed(
        {
            "src/app/a.py": "from app.b import value\n",
            "src/app/b.py": "from app.a import value\n",
            "src/app/c.py": "value = 1\n",
        }
    )

    cycles = strongly_connected_components(build_import_graph(parsed))

    assert cycles == {("app.a", "app.b")}


def test_import_graph_resolves_relative_imports_from_package_initializers() -> None:
    parsed = _parsed(
        {
            "src/app/package/__init__.py": "from .worker import value\n",
            "src/app/package/worker.py": "from app.package import other\n",
        }
    )

    cycles = strongly_connected_components(build_import_graph(parsed))

    assert cycles == {("app.package", "app.package.worker")}


def test_layer_policy_rejects_route_to_integration_and_allows_application() -> None:
    assert invalid_layer_dependency("app.api.routes.chat", "app.integrations.pipefacil")
    assert invalid_layer_dependency("app.api.routes.chat", "app.application.chat") is None


def test_node_contract_rejects_state_mutation_full_return_and_unknown_keys() -> None:
    parsed = _parsed(
        {
            "src/app/agent/nodes/bad.py": """def bad_node(state):
    state["status"] = "bad"
    state["nested"]["value"] = "bad"
    state["items"].append("bad")
    state.update({"status": "worse"})
    if state.get("done"):
        return state
    return {"missing": True}
"""
        }
    )["app.agent.nodes.bad"]

    findings = node_contract_findings(parsed, {"status", "done"})

    assert {finding.rule_id for finding in findings} == {
        "LG-NODE-001",
        "LG-NODE-002",
        "LG-STATE-001",
    }


def test_node_contract_accepts_partial_state_update() -> None:
    parsed = _parsed(
        {
            "src/app/agent/nodes/good.py": """def good_node(state):
    current = state.get("status")
    return {"status": current or "ready"}
"""
        }
    )["app.agent.nodes.good"]

    assert node_contract_findings(parsed, {"status"}) == []


def test_general_complexity_ratchet_warns_at_11_and_blocks_at_15() -> None:
    warning_body = "\n".join(
        f"    if value == {index}:\n        return value" for index in range(10)
    )
    blocker_body = "\n".join(
        f"    if value == {index}:\n        return value" for index in range(14)
    )
    path = "src/app/example.py"
    sources = _parsed(
        {
            path: (
                f"def warning(value):\n{warning_body}\n    return -1\n\n"
                f"def blocker(value):\n{blocker_body}\n    return -1\n"
            )
        }
    )
    changes = DiffChanges(lines_by_path={path: set(range(1, 100))}, paths={path})

    findings = structure_findings(sources, sources, changes, QUALITY_CONFIG)
    complexity_findings = {
        (finding.rule_id, finding.severity)
        for finding in findings
        if finding.rule_id.startswith("PY-COMPLEXITY")
    }

    assert complexity_findings == {
        ("PY-COMPLEXITY-001", "blocker"),
        ("PY-COMPLEXITY-002", "warning"),
    }
    untouched = structure_findings(sources, sources, DiffChanges({}, set()), QUALITY_CONFIG)
    assert not any(finding.rule_id.startswith("PY-COMPLEXITY") for finding in untouched)


def test_layer_ratchet_blocks_only_new_invalid_dependencies() -> None:
    route_path = "src/app/api/routes/example.py"
    current = _parsed(
        {
            route_path: "from app.integrations.example import client\n",
            "src/app/integrations/example.py": "client = object()\n",
        }
    )
    changes = DiffChanges(lines_by_path={route_path: {1}}, paths={route_path})

    new_findings = structure_findings(current, {}, changes, QUALITY_CONFIG)
    legacy_findings = structure_findings(current, current, changes, QUALITY_CONFIG)

    assert "ARCH-LAYER-001" in {finding.rule_id for finding in new_findings}
    assert "ARCH-LAYER-001" not in {finding.rule_id for finding in legacy_findings}


def test_structure_ratchet_enforces_node_complexity_size_and_import_limits() -> None:
    body = "\n".join(f"    value_{index} = {index}" for index in range(35))
    decisions = "\n".join(
        f"    if value_{index}:\n        value_{index} += 1" for index in range(8)
    )
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/routing.py": "from app.agent.state import AgentState\n",
            "src/app/agent/graph.py": """from app.agent.nodes.busy import busy
def build_graph(builder):
    builder.add_node("busy", busy)
""",
            "src/app/agent/nodes/busy.py": (
                "from app.agent.state import AgentState\n"
                "from app.agent.messages import latest_user_message\n"
                "from app.agent.chains import build_classifier_chain\n"
                "from app.agent.prompts import get_classifier_prompt_template\n"
                "from app.core.config import get_settings\n\n"
                "def busy(state: AgentState) -> dict[str, str]:\n"
                f"{body}\n{decisions}\n"
                '    return {"status": "done"}\n'
            ),
        }
    )
    node_path = "src/app/agent/nodes/busy.py"
    changes = DiffChanges(
        lines_by_path={node_path: set(range(1, 200))},
        paths={node_path},
    )

    findings = structure_findings(sources, {}, changes, QUALITY_CONFIG)
    rule_ids = {finding.rule_id for finding in findings}

    assert {"PY-NODE-001", "PY-NODE-002", "PY-NODE-003"} <= rule_ids


def test_graph_contract_rejects_static_and_dynamic_routing_on_same_node() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """from app.agent.state import AgentState
def decide(state: AgentState) -> dict[str, str]:
    return {"status": "ok"}
def finish(state: AgentState) -> dict[str, str]:
    return {"status": "done"}
""",
            "src/app/agent/routing.py": """from typing import Literal
from app.agent.state import AgentState
DECIDE = "decide"
FINISH = "finish"
def route(state: AgentState) -> Literal["finish"]:
    return FINISH
""",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide, finish
from app.agent.routing import DECIDE, FINISH, route
def build_graph(builder):
    builder.add_node(DECIDE, decide)
    builder.add_node(FINISH, finish)
    builder.add_edge(DECIDE, FINISH)
    builder.add_conditional_edges(DECIDE, route)
""",
        }
    )

    findings = graph_contract_findings(sources)

    assert "LG-EDGE-001" in {finding.rule_id for finding in findings}


def test_graph_contract_rejects_impure_router_and_unknown_literal_destination() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """from app.agent.state import AgentState
def decide(state: AgentState) -> dict[str, str]:
    return {"status": "ok"}
""",
            "src/app/agent/routing.py": """from typing import Literal
import httpx
from app.agent.state import AgentState
def route(state: AgentState) -> Literal["missing"]:
    return "missing"
""",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide
from app.agent.routing import route
def build_graph(builder):
    builder.add_node("decide", decide)
    builder.add_conditional_edges("decide", route)
""",
        }
    )

    findings = graph_contract_findings(sources)
    rule_ids = {finding.rule_id for finding in findings}

    assert {"LG-ROUTE-001", "LG-ROUTE-004"} <= rule_ids


def test_graph_contract_requires_literal_annotation_for_command_node() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """from langgraph.types import Command
from app.agent.state import AgentState
def decide(state: AgentState) -> Command:
    return Command(update={"status": "ok"}, goto="finish")
def finish(state: AgentState) -> dict[str, str]:
    return {"status": "done"}
""",
            "src/app/agent/routing.py": "from app.agent.state import AgentState\n",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide, finish
def build_graph(builder):
    builder.add_node("decide", decide)
    builder.add_node("finish", finish)
""",
        }
    )

    findings = graph_contract_findings(sources)

    assert "LG-CMD-001" in {finding.rule_id for finding in findings}


def test_graph_contract_rejects_command_goto_outside_declared_literal() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """from typing import Literal
from langgraph.types import Command
from app.agent.state import AgentState
def decide(state: AgentState) -> Command[Literal["finish"]]:
    return Command(update={"status": "ok"}, goto="missing")
def finish(state: AgentState) -> dict[str, str]:
    return {"status": "done"}
""",
            "src/app/agent/routing.py": "from app.agent.state import AgentState\n",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide, finish
def build_graph(builder):
    builder.add_node("decide", decide)
    builder.add_node("finish", finish)
""",
        }
    )

    findings = graph_contract_findings(sources)

    assert "LG-CMD-001" in {finding.rule_id for finding in findings}
    assert any("goto missing from Literal missing" in finding.message for finding in findings)


def test_graph_contract_requires_agent_state_node_signature() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """def decide(state) -> dict[str, str]:
    return {"status": "ok"}
""",
            "src/app/agent/routing.py": "from app.agent.state import AgentState\n",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide
def build_graph(builder):
    builder.add_node("decide", decide)
""",
        }
    )

    findings = graph_contract_findings(sources)

    assert "LG-NODE-003" in {finding.rule_id for finding in findings}


def test_graph_contract_rejects_router_state_mutation() -> None:
    sources = _parsed(
        {
            "src/app/agent/state.py": """from typing_extensions import TypedDict
class AgentState(TypedDict):
    status: str
""",
            "src/app/agent/nodes/example.py": """from app.agent.state import AgentState
def decide(state: AgentState) -> dict[str, str]:
    return {"status": "ok"}
""",
            "src/app/agent/routing.py": """from typing import Literal
from app.agent.state import AgentState
def route(state: AgentState) -> Literal["decide"]:
    state.update({"status": "changed"})
    return "decide"
""",
            "src/app/agent/graph.py": """from app.agent.nodes.example import decide
from app.agent.routing import route
def build_graph(builder):
    builder.add_node("decide", decide)
    builder.add_conditional_edges("decide", route)
""",
        }
    )

    findings = graph_contract_findings(sources)

    assert "LG-ROUTE-001" in {finding.rule_id for finding in findings}


def test_parse_cobertura_and_evaluate_changed_lines_and_branches(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    report.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <packages><package><classes>
    <class filename="src/app/example.py"><lines>
      <line number="2" hits="1" branch="true" condition-coverage="50% (1/2)" />
      <line number="3" hits="0" />
    </lines></class>
  </classes></package></packages>
</coverage>
"""
    )

    coverage = parse_cobertura_coverage(report)
    result = evaluate_diff_coverage({"src/app/example.py": {1, 2, 3}}, coverage)

    assert result.line_covered == 1
    assert result.line_total == 2
    assert result.branch_covered == 1
    assert result.branch_total == 2
    assert result.uncovered_lines == ("src/app/example.py:3",)
    assert result.partial_branches == ("src/app/example.py:2 (1/2)",)


def test_diff_coverage_without_executable_lines_or_branches_is_complete() -> None:
    result = evaluate_diff_coverage(
        {"src/app/example.py": {1}},
        {"src/app/example.py": {2: CoveredLine(hits=0)}},
    )

    assert result.line_percent == 100.0
    assert result.branch_percent == 100.0
