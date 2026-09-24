#!/usr/bin/env python3
"""Check changed Python complexity and repository-wide architecture contracts."""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from scripts.quality.common import (
    BaseReference,
    DiffChanges,
    QualityConfig,
    QualityGateError,
    changed_lines,
    git_output,
    load_quality_config,
    repository_root,
    resolve_base_reference,
)


@dataclass(frozen=True, order=True)
class Finding:
    rule_id: str
    path: str
    message: str
    severity: str = "blocker"


@dataclass(frozen=True)
class SourceModule:
    path: str
    module: str
    source: str
    tree: ast.Module
    imports: frozenset[str]


@dataclass(frozen=True)
class FunctionMetric:
    path: str
    name: str
    start: int
    end: int
    complexity: int
    effective_lines: int


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
FunctionLocation = tuple[SourceModule, FunctionNode]


@dataclass(frozen=True)
class GraphDefinition:
    graph_module: SourceModule
    routing_module: SourceModule
    constants: dict[str, str]
    registered: dict[str, str]
    static_sources: frozenset[str]
    conditional_sources: frozenset[str]
    routers: frozenset[str]


def _module_name(path: str, source_root: str) -> str:
    source_parent = Path(source_root).parent
    relative = Path(path).relative_to(source_parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative_import(
    module: str,
    *,
    current_module: str,
    level: int,
    is_package: bool,
) -> str:
    package_parts = current_module.split(".")
    if not is_package:
        package_parts.pop()
    keep = len(package_parts) - max(level - 1, 0)
    base = package_parts[: max(keep, 0)]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def import_targets(
    tree: ast.Module,
    *,
    current_module: str,
    is_package: bool = False,
) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                targets.add(
                    _resolve_relative_import(
                        node.module or "",
                        current_module=current_module,
                        level=node.level,
                        is_package=is_package,
                    )
                )
            elif node.module:
                targets.add(node.module)
    return {target for target in targets if target}


def parse_sources(sources: dict[str, str], source_root: str) -> dict[str, SourceModule]:
    result: dict[str, SourceModule] = {}
    for path, source in sources.items():
        module = _module_name(path, source_root)
        tree = ast.parse(source, filename=path)
        result[module] = SourceModule(
            path=path,
            module=module,
            source=source,
            tree=tree,
            imports=frozenset(
                import_targets(
                    tree,
                    current_module=module,
                    is_package=Path(path).name == "__init__.py",
                )
            ),
        )
    return result


def current_python_sources(repo_root: Path, source_root: str) -> dict[str, str]:
    root = repo_root / source_root
    return {
        str(path.relative_to(repo_root)): path.read_text()
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def python_sources_at_ref(
    repo_root: Path,
    revision: str,
    source_root: str,
) -> dict[str, str]:
    paths = git_output(repo_root, "ls-tree", "-r", "--name-only", revision, "--", source_root)
    result: dict[str, str] = {}
    for path in paths.splitlines():
        if not path.endswith(".py"):
            continue
        result[path] = git_output(repo_root, "show", f"{revision}:{path}")
    return result


def _resolved_internal_target(target: str, modules: set[str]) -> str | None:
    if target in modules:
        return target
    candidates = [module for module in modules if target.startswith(f"{module}.")]
    return max(candidates, key=len) if candidates else None


def build_import_graph(sources: dict[str, SourceModule]) -> dict[str, set[str]]:
    modules = set(sources)
    graph = {module: set() for module in modules}
    for module, parsed in sources.items():
        for target in parsed.imports:
            resolved = _resolved_internal_target(target, modules)
            if resolved and resolved != module:
                graph[module].add(resolved)
    return graph


def strongly_connected_components(graph: dict[str, set[str]]) -> set[tuple[str, ...]]:
    next_index = 0
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        nonlocal next_index
        indices[node] = low_links[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)

        for target in graph.get(node, set()):
            if target not in indices:
                visit(target)
                low_links[node] = min(low_links[node], low_links[target])
            elif target in on_stack:
                low_links[node] = min(low_links[node], indices[target])

        if low_links[node] != indices[node]:
            return
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            components.add(tuple(sorted(component)))

    for node in graph:
        if node not in indices:
            visit(node)
    return components


FORBIDDEN_LAYER_EDGES = (
    (
        ("app.api.routes",),
        ("app.agent", "app.integrations"),
        "HTTP routes must delegate through application instead of agent/integrations.",
    ),
    (
        ("app.application",),
        ("app.api",),
        "Application must not depend on the HTTP API layer.",
    ),
    (
        ("app.integrations",),
        ("app.api", "app.application", "app.agent"),
        "Integrations must not depend on API, application, or agent.",
    ),
    (
        ("app.agent",),
        ("app.api",),
        "Agent must not depend on the HTTP API layer.",
    ),
    (
        ("app.agent.nodes", "app.agent.chains", "app.agent.routing", "app.agent.state"),
        ("app.application", "app.integrations"),
        "Agent nodes, chains, routing, and state must not own external integrations.",
    ),
)
LIMITED_LAYER_DEPENDENCIES = (
    (
        ("app.observability",),
        ("app.observability", "app.core"),
        "Observability may depend only on observability/core application modules.",
    ),
    (
        ("app.core",),
        ("app.core",),
        "Core must not depend on higher application layers.",
    ),
    (
        ("app.outbound_media",),
        ("app.outbound_media", "app.core"),
        "Outbound media may depend only on outbound_media/core.",
    ),
)
SDK_OWNERS = {
    "agents": (
        ("app.agent.specialists",),
        "OpenAI Agents SDK imports belong to agent/specialists.",
    ),
    "fastapi": (("app.api", "app.main"), "FastAPI imports belong to api or app.main."),
    "httpx": (("app.integrations",), "httpx imports belong to integrations."),
    "langfuse": (("app.observability",), "Langfuse SDK imports belong to observability."),
    "langgraph": (("app.agent",), "LangGraph imports belong to agent."),
}


def invalid_layer_dependency(source: str, target: str) -> str | None:
    for source_prefixes, target_prefixes, reason in FORBIDDEN_LAYER_EDGES:
        if source.startswith(source_prefixes) and target.startswith(target_prefixes):
            return reason
    for source_prefixes, allowed_prefixes, reason in LIMITED_LAYER_DEPENDENCIES:
        if source.startswith(source_prefixes) and not target.startswith(allowed_prefixes):
            return reason
    return None


def invalid_sdk_owner(source: str, target: str) -> str | None:
    policy = SDK_OWNERS.get(target.split(".", 1)[0])
    if policy is None:
        return None
    owner_prefixes, reason = policy
    return None if source.startswith(owner_prefixes) else reason


def internal_edges(sources: dict[str, SourceModule]) -> set[tuple[str, str]]:
    modules = set(sources)
    edges: set[tuple[str, str]] = set()
    for source, parsed in sources.items():
        for target in parsed.imports:
            resolved = _resolved_internal_target(target, modules)
            if resolved and resolved != source:
                edges.add((source, resolved))
    return edges


def _walk_function(node: ast.AST) -> Iterable[ast.AST]:
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield current
        stack.extend(ast.iter_child_nodes(current))


def cyclomatic_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    score = 1
    for child in _walk_function(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.Assert)):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(len(child.values) - 1, 1)
        elif isinstance(child, ast.ExceptHandler):
            score += 1
        elif isinstance(child, ast.match_case):
            score += 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
    return score


def _effective_lines(source: str, start: int, end: int) -> int:
    lines = source.splitlines()[start - 1 : end]
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, path: str, source: str) -> None:
        self.path = path
        self.source = source
        self.scope: list[str] = []
        self.metrics: list[FunctionMetric] = []

    def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
        end = node.end_lineno or node.lineno
        qualified_name = ".".join([*self.scope, node.name])
        self.metrics.append(
            FunctionMetric(
                path=self.path,
                name=qualified_name,
                start=start,
                end=end,
                complexity=cyclomatic_complexity(node),
                effective_lines=_effective_lines(self.source, start, end),
            )
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def function_metrics(parsed: SourceModule) -> list[FunctionMetric]:
    collector = _FunctionCollector(parsed.path, parsed.source)
    collector.visit(parsed.tree)
    return collector.metrics


def state_keys(parsed_state: SourceModule) -> set[str]:
    for node in parsed_state.tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AgentState":
            return {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    raise QualityGateError("AgentState was not found in src/app/agent/state.py.")


def _is_state_subscript(node: ast.AST, state_name: str) -> bool:
    return isinstance(node, ast.Subscript) and _root_name(node) == state_name


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.Assign, ast.Delete)):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    return []


def _assignment_mutates_state(node: ast.AST, state_name: str) -> bool:
    return any(
        _is_state_subscript(candidate, state_name)
        for target in _assignment_targets(node)
        for candidate in ast.walk(target)
    )


def _literal_dict_keys(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


STATE_MUTATING_METHODS = {
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
    "__delitem__",
    "__setitem__",
}


def _mutates_state_via_call(node: ast.Call, state_name: str) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in STATE_MUTATING_METHODS
        and _root_name(node.func.value) == state_name
    )


def _state_mutation_findings(
    parsed: SourceModule,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    state_name: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for child in _walk_function(function):
        if _assignment_mutates_state(child, state_name):
            findings.append(
                Finding(
                    "LG-NODE-001",
                    f"{parsed.path}:{getattr(child, 'lineno', function.lineno)}",
                    f"{function.name} mutates AgentState directly; return a partial update.",
                )
            )
        if not isinstance(child, ast.Call) or not _mutates_state_via_call(child, state_name):
            continue
        method_name = child.func.attr if isinstance(child.func, ast.Attribute) else "mutator"
        findings.append(
            Finding(
                "LG-NODE-001",
                f"{parsed.path}:{child.lineno}",
                f"{function.name} mutates AgentState via state.{method_name}().",
            )
        )
    return findings


def _return_contract_findings(
    parsed: SourceModule,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    state_name: str,
    known_state_keys: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    for child in _walk_function(function):
        if not isinstance(child, ast.Return):
            continue
        if isinstance(child.value, ast.Name) and child.value.id == state_name:
            findings.append(
                Finding(
                    "LG-NODE-002",
                    f"{parsed.path}:{child.lineno}",
                    f"{function.name} returns the full state instead of a partial update.",
                )
            )
        return_payload = child.value
        update_payload: ast.AST | None = None
        if isinstance(return_payload, ast.Dict):
            update_payload = return_payload
        elif isinstance(return_payload, ast.Call) and _call_name(return_payload) == "Command":
            update_payload = next(
                (keyword.value for keyword in return_payload.keywords if keyword.arg == "update"),
                None,
            )
        unknown = _literal_dict_keys(update_payload) - known_state_keys
        if unknown:
            findings.append(
                Finding(
                    "LG-STATE-001",
                    f"{parsed.path}:{child.lineno}",
                    f"{function.name} returns unknown AgentState keys: "
                    f"{', '.join(sorted(unknown))}.",
                )
            )
    return findings


def node_contract_findings(parsed: SourceModule, known_state_keys: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    functions = (
        node
        for node in ast.walk(parsed.tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for function in functions:
        positional_parameters = [*function.args.posonlyargs, *function.args.args]
        if not positional_parameters or positional_parameters[0].arg != "state":
            continue
        state_name = positional_parameters[0].arg
        findings.extend(_state_mutation_findings(parsed, function, state_name))
        findings.extend(_return_contract_findings(parsed, function, state_name, known_state_keys))
    return findings


def _string_constants(parsed: SourceModule) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in parsed.tree.body:
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    return values


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _name_or_constant(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, node.id)
    return None


def _function_uses_command(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call) and _call_name(node) == "Command"
        for node in _walk_function(function)
    )


def _annotation_text(annotation: ast.AST | None) -> str:
    if annotation is None:
        return ""
    text = ast.unparse(annotation)
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value
    return text


def _is_agent_state_annotation(annotation: ast.AST | None) -> bool:
    text = _annotation_text(annotation)
    return text == "AgentState" or text.endswith(".AgentState")


def _literal_destinations(annotation: ast.AST | None) -> set[str]:
    if annotation is None:
        return set()
    destinations: set[str] = set()
    for node in ast.walk(annotation):
        if not isinstance(node, ast.Subscript):
            continue
        value = ast.unparse(node.value)
        if value != "Literal" and not value.endswith(".Literal"):
            continue
        destinations.update(
            child.value
            for child in ast.walk(node.slice)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        )
    return destinations


def _static_destinations(node: ast.AST, constants: dict[str, str]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        resolved = constants.get(node.id)
        return {resolved} if resolved is not None else set()
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return {
            destination
            for item in node.elts
            for destination in _static_destinations(item, constants)
        }
    return set()


ROUTER_PURE_BUILTINS = {
    "all",
    "any",
    "bool",
    "float",
    "int",
    "isinstance",
    "len",
    "max",
    "min",
    "str",
    "tuple",
}
ROUTER_PURE_METHODS = {
    "endswith",
    "get",
    "isalnum",
    "isalpha",
    "isdigit",
    "islower",
    "isnumeric",
    "isspace",
    "isupper",
    "lower",
    "startswith",
    "strip",
    "upper",
}


def _router_is_pure(function: ast.FunctionDef | ast.AsyncFunctionDef, state_name: str) -> bool:
    for child in _walk_function(function):
        if isinstance(child, (ast.Await, ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom)):
            return False
        if _assignment_mutates_state(child, state_name):
            return False
        if not isinstance(child, ast.Call):
            continue
        if _mutates_state_via_call(child, state_name):
            return False
        if isinstance(child.func, ast.Name) and child.func.id in ROUTER_PURE_BUILTINS:
            continue
        if isinstance(child.func, ast.Attribute) and child.func.attr in ROUTER_PURE_METHODS:
            continue
        return False
    return True


def _inspect_graph_definition(
    graph_module: SourceModule,
    routing_module: SourceModule,
) -> GraphDefinition:
    constants = {**_string_constants(routing_module), **_string_constants(graph_module)}
    registered: dict[str, str] = {}
    static_sources: set[str] = set()
    conditional_sources: set[str] = set()
    routers: set[str] = set()
    for node in ast.walk(graph_module.tree):
        if not isinstance(node, ast.Call):
            continue
        call = _call_name(node)
        if call == "add_node" and node.args:
            callable_arg = node.args[1] if len(node.args) >= 2 else node.args[0]
            callable_name = ast.unparse(callable_arg)
            graph_name = (
                _name_or_constant(node.args[0], constants)
                if len(node.args) >= 2
                else callable_name.rsplit(".", 1)[-1]
            )
            if graph_name:
                registered[graph_name] = callable_name
        elif call == "add_edge" and node.args:
            source = _name_or_constant(node.args[0], constants)
            if source:
                static_sources.add(source)
        elif call == "add_conditional_edges" and len(node.args) >= 2:
            source = _name_or_constant(node.args[0], constants)
            if source:
                conditional_sources.add(source)
            routers.add(ast.unparse(node.args[1]))
    return GraphDefinition(
        graph_module=graph_module,
        routing_module=routing_module,
        constants=constants,
        registered=registered,
        static_sources=frozenset(static_sources),
        conditional_sources=frozenset(conditional_sources),
        routers=frozenset(routers),
    )


def _index_functions(
    sources: dict[str, SourceModule],
) -> tuple[dict[str, FunctionLocation], dict[str, FunctionLocation]]:
    functions: dict[str, FunctionLocation] = {}
    node_functions: dict[str, FunctionLocation] = {}
    for parsed in sources.values():
        for node in parsed.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            functions[node.name] = (parsed, node)
            if parsed.module.startswith("app.agent.nodes"):
                node_functions[node.name] = (parsed, node)
    return functions, node_functions


def _node_signature_findings(
    parsed: SourceModule,
    function: FunctionNode,
    callable_name: str,
) -> list[Finding]:
    findings: list[Finding] = []
    positional = [*function.args.posonlyargs, *function.args.args]
    extras = [*positional[1:], *function.args.kwonlyargs]
    signature_valid = (
        bool(positional)
        and positional[0].arg == "state"
        and _is_agent_state_annotation(positional[0].annotation)
        and len(extras) <= 2
        and function.args.vararg is None
        and function.args.kwarg is None
    )
    if not signature_valid:
        findings.append(
            Finding(
                "LG-NODE-003",
                f"{parsed.path}:{function.lineno}",
                f"Node {callable_name} must accept state: AgentState and only "
                "config/runtime arguments.",
            )
        )
    for parameter in extras:
        annotation = _annotation_text(parameter.annotation)
        valid_extra = parameter.arg in {"config", "runtime"} and any(
            allowed in annotation for allowed in ("RunnableConfig", "Runtime")
        )
        if not valid_extra:
            findings.append(
                Finding(
                    "LG-NODE-003",
                    f"{parsed.path}:{function.lineno}",
                    f"Unsupported node parameter {parameter.arg!r} in {callable_name}.",
                )
            )
    if not any(allowed in _annotation_text(function.returns) for allowed in ("dict", "Command")):
        findings.append(
            Finding(
                "LG-NODE-004",
                f"{parsed.path}:{function.lineno}",
                f"Node {callable_name} needs a dict or Command return annotation.",
            )
        )
    return findings


def _command_contract_finding(
    definition: GraphDefinition,
    parsed: SourceModule,
    function: FunctionNode,
    callable_name: str,
) -> Finding | None:
    declared = _literal_destinations(function.returns)
    command_constants = {**definition.constants, **_string_constants(parsed)}
    gotos = {
        destination
        for node in _walk_function(function)
        if isinstance(node, ast.Call) and _call_name(node) == "Command"
        for keyword in node.keywords
        if keyword.arg == "goto"
        for destination in _static_destinations(keyword.value, command_constants)
    }
    invalid = declared - set(definition.registered) - {"__end__"}
    mismatched = gotos - declared
    return_annotation = _annotation_text(function.returns)
    if "Command" in return_annotation and declared and not invalid and not mismatched:
        return None

    details: list[str] = []
    if invalid:
        details.append("unknown destinations " + ", ".join(sorted(invalid)))
    if mismatched:
        details.append("goto missing from Literal " + ", ".join(sorted(mismatched)))
    suffix = f" ({'; '.join(details)})" if details else ""
    return Finding(
        "LG-CMD-001",
        f"{parsed.path}:{function.lineno}",
        f"Command node {callable_name} must declare valid "
        f"Command[Literal[...]] destinations{suffix}.",
    )


def _registered_node_findings(
    definition: GraphDefinition,
    functions: dict[str, FunctionLocation],
    node_functions: dict[str, FunctionLocation],
) -> tuple[list[Finding], set[str]]:
    findings: list[Finding] = []
    command_nodes: set[str] = set()
    for graph_name, callable_name in definition.registered.items():
        function_name = callable_name.rsplit(".", 1)[-1]
        located = node_functions.get(function_name) or functions.get(function_name)
        if located is None:
            findings.append(
                Finding(
                    "LG-GRAPH-002",
                    definition.graph_module.path,
                    f"Registered node callable {callable_name!r} could not be inspected.",
                )
            )
            continue
        parsed, function = located
        findings.extend(_node_signature_findings(parsed, function, callable_name))
        if not _function_uses_command(function):
            continue
        command_nodes.add(graph_name)
        command_finding = _command_contract_finding(
            definition,
            parsed,
            function,
            callable_name,
        )
        if command_finding:
            findings.append(command_finding)
    return findings, command_nodes


def _routing_import_finding(definition: GraphDefinition) -> Finding | None:
    allowlist = {"__future__", "typing", "app.agent.state"}
    invalid = {
        target
        for target in definition.routing_module.imports
        if target.split(".", 1)[0] not in allowlist and target not in allowlist
    }
    if not invalid:
        return None
    return Finding(
        "LG-ROUTE-001",
        definition.routing_module.path,
        "Routing must stay pure; invalid imports: " + ", ".join(sorted(invalid)),
    )


def _router_signature_valid(function: FunctionNode) -> bool:
    positional = [*function.args.posonlyargs, *function.args.args]
    return (
        len(positional) == 1
        and positional[0].arg == "state"
        and _is_agent_state_annotation(positional[0].annotation)
        and not function.args.kwonlyargs
        and function.args.vararg is None
        and function.args.kwarg is None
    )


def _router_contract_findings(definition: GraphDefinition) -> list[Finding]:
    findings: list[Finding] = []
    router_functions = {
        node.name: node
        for node in definition.routing_module.tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for router_name in definition.routers:
        function_name = router_name.rsplit(".", 1)[-1]
        function = router_functions.get(function_name)
        if function is None:
            findings.append(
                Finding(
                    "LG-ROUTE-002",
                    definition.routing_module.path,
                    f"Router {router_name!r} was not found.",
                )
            )
            continue
        positional = [*function.args.posonlyargs, *function.args.args]
        state_name = positional[0].arg if positional else "state"
        location = f"{definition.routing_module.path}:{function.lineno}"
        if not _router_signature_valid(function) or not _router_is_pure(function, state_name):
            findings.append(
                Finding(
                    "LG-ROUTE-001",
                    location,
                    f"Router {router_name} must be a pure state: AgentState function.",
                )
            )
        destinations = _literal_destinations(function.returns)
        if not destinations:
            findings.append(
                Finding(
                    "LG-ROUTE-003",
                    location,
                    f"Router {router_name} must return Literal destinations.",
                )
            )
            continue
        unknown = destinations - set(definition.registered) - {"__end__"}
        if unknown:
            findings.append(
                Finding(
                    "LG-ROUTE-004",
                    location,
                    f"Router {router_name} targets unknown nodes: {', '.join(sorted(unknown))}.",
                )
            )
    return findings


def graph_contract_findings(sources: dict[str, SourceModule]) -> list[Finding]:
    findings: list[Finding] = []
    graph_module = sources.get("app.agent.graph")
    routing_module = sources.get("app.agent.routing")
    if graph_module is None or routing_module is None:
        return [Finding("LG-GRAPH-001", "src/app/agent", "Graph or routing module is missing.")]
    definition = _inspect_graph_definition(graph_module, routing_module)
    function_by_name, node_function_by_name = _index_functions(sources)

    node_findings, command_nodes = _registered_node_findings(
        definition,
        function_by_name,
        node_function_by_name,
    )
    findings.extend(node_findings)

    for source in sorted(
        definition.static_sources & (definition.conditional_sources | command_nodes)
    ):
        findings.append(
            Finding(
                "LG-EDGE-001",
                graph_module.path,
                f"Node {source!r} mixes a static edge with dynamic routing.",
            )
        )

    routing_import_finding = _routing_import_finding(definition)
    if routing_import_finding:
        findings.append(routing_import_finding)
    findings.extend(_router_contract_findings(definition))
    return findings


def _function_metric_findings(
    metric: FunctionMetric,
    node_prefix: str,
    config: QualityConfig,
) -> list[Finding]:
    location = f"{metric.path}:{metric.start} ({metric.name})"
    if metric.path.startswith(node_prefix):
        findings: list[Finding] = []
        if metric.complexity > config.node_complexity_max:
            findings.append(
                Finding(
                    "PY-NODE-001",
                    location,
                    f"Complexity {metric.complexity} exceeds node maximum "
                    f"{config.node_complexity_max}.",
                )
            )
        if metric.effective_lines > config.node_max_lines:
            findings.append(
                Finding(
                    "PY-NODE-002",
                    location,
                    f"{metric.effective_lines} effective lines exceed node maximum "
                    f"{config.node_max_lines}.",
                )
            )
        return findings
    if metric.complexity > config.complexity_max:
        return [
            Finding(
                "PY-COMPLEXITY-001",
                location,
                f"Complexity {metric.complexity} exceeds maximum {config.complexity_max}.",
            )
        ]
    if metric.complexity >= config.complexity_warning:
        return [
            Finding(
                "PY-COMPLEXITY-002",
                location,
                f"Complexity {metric.complexity} should be simplified or justified.",
                severity="warning",
            )
        ]
    return []


def _changed_complexity_findings(
    current: dict[str, SourceModule],
    changes: DiffChanges,
    node_prefix: str,
    config: QualityConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in current.values():
        changed = changes.lines_by_path.get(parsed.path, set())
        for metric in function_metrics(parsed):
            if any(metric.start <= line <= metric.end for line in changed):
                findings.extend(_function_metric_findings(metric, node_prefix, config))
    return findings


def _node_import_findings(
    current: dict[str, SourceModule],
    changes: DiffChanges,
    node_prefix: str,
    maximum: int,
) -> list[Finding]:
    findings: list[Finding] = []
    for parsed in current.values():
        is_changed_node = (
            parsed.path.startswith(node_prefix)
            and Path(parsed.path).name != "__init__.py"
            and parsed.path in changes.paths
        )
        if not is_changed_node:
            continue
        internal_imports = {target for target in parsed.imports if target.startswith("app.")}
        if len(internal_imports) <= maximum:
            continue
        findings.append(
            Finding(
                "PY-NODE-003",
                parsed.path,
                f"Node module has {len(internal_imports)} internal imports; maximum is "
                f"{maximum}: {', '.join(sorted(internal_imports))}.",
            )
        )
    return findings


def _cycle_findings(current: dict[str, SourceModule]) -> list[Finding]:
    graph = build_import_graph(current)
    return [
        Finding("ARCH-CYCLE-001", cycle[0], "Import cycle: " + " -> ".join(cycle))
        for cycle in sorted(strongly_connected_components(graph))
    ]


def _layer_findings(
    current: dict[str, SourceModule],
    baseline: dict[str, SourceModule],
) -> list[Finding]:
    baseline_edges = internal_edges(baseline)
    findings: list[Finding] = []
    for source, target in sorted(internal_edges(current) - baseline_edges):
        reason = invalid_layer_dependency(source, target)
        if reason:
            findings.append(
                Finding(
                    "ARCH-LAYER-001",
                    current[source].path,
                    f"Invalid dependency {source} -> {target}. {reason}",
                )
            )
    return findings


def _sdk_owner_findings(current: dict[str, SourceModule]) -> list[Finding]:
    findings: list[Finding] = []
    for source, parsed in current.items():
        for target in sorted(parsed.imports):
            reason = invalid_sdk_owner(source, target)
            if reason:
                findings.append(
                    Finding(
                        "ARCH-SDK-001",
                        parsed.path,
                        f"Invalid SDK ownership for {target!r}. {reason}",
                    )
                )
    return findings


def _agent_state_findings(
    current: dict[str, SourceModule],
    node_prefix: str,
    source_root: str,
) -> list[Finding]:
    state_module = current.get("app.agent.state")
    if state_module is None:
        return [Finding("LG-STATE-001", source_root, "AgentState module is missing.")]
    known_state_keys = state_keys(state_module)
    findings: list[Finding] = []
    for parsed in current.values():
        if parsed.path.startswith(node_prefix):
            findings.extend(node_contract_findings(parsed, known_state_keys))
    return findings


def structure_findings(
    current: dict[str, SourceModule],
    baseline: dict[str, SourceModule],
    changes: DiffChanges,
    config: QualityConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    node_prefix = f"{config.node_root.rstrip('/')}/"
    findings.extend(_changed_complexity_findings(current, changes, node_prefix, config))
    findings.extend(
        _node_import_findings(
            current,
            changes,
            node_prefix,
            config.node_max_internal_imports,
        )
    )
    findings.extend(_cycle_findings(current))
    findings.extend(_layer_findings(current, baseline))
    findings.extend(_sdk_owner_findings(current))
    findings.extend(_agent_state_findings(current, node_prefix, config.source_root))
    findings.extend(graph_contract_findings(current))
    return sorted(set(findings))


def render_report(
    base: BaseReference,
    config: QualityConfig,
    findings: list[Finding],
) -> str:
    blockers = [finding for finding in findings if finding.severity == "blocker"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    status = "FALHOU" if blockers else "PASSOU"
    lines = [
        "## Estrutura Python e contratos do agente",
        "",
        f"- Base: `{base.requested}` (comparacao `{base.revision}`)",
        f"- Status: **{status}**",
        f"- Complexidade geral: {config.complexity_warning}-"
        f"{config.complexity_max} alerta; {config.complexity_max + 1}+ bloqueia.",
        f"- Nodes alterados: complexidade <= {config.node_complexity_max}, "
        f"linhas efetivas <= {config.node_max_lines}, imports internos <= "
        f"{config.node_max_internal_imports}.",
        "",
        "### Bloqueios",
        "",
    ]
    lines.extend(
        f"- `{finding.rule_id}` `{finding.path}` — {finding.message}" for finding in blockers
    )
    if not blockers:
        lines.append("- Nenhum.")
    lines.extend(["", "### Alertas", ""])
    lines.extend(
        f"- `{finding.rule_id}` `{finding.path}` — {finding.message}" for finding in warnings
    )
    if not warnings:
        lines.append("- Nenhum.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--report", default="build/quality/python-structure.md")
    args = parser.parse_args()

    repo_root = repository_root()
    try:
        config = load_quality_config(repo_root)
        base = resolve_base_reference(repo_root, args.base)
        changes = changed_lines(repo_root, base.revision, config.source_root)
        current = parse_sources(
            current_python_sources(repo_root, config.source_root),
            config.source_root,
        )
        baseline = parse_sources(
            python_sources_at_ref(repo_root, base.revision, config.source_root),
            config.source_root,
        )
        findings = structure_findings(current, baseline, changes, config)
        report = render_report(base, config, findings)
    except (OSError, SyntaxError, QualityGateError, KeyError, ValueError) as exc:
        report = f"## Estrutura Python e contratos do agente\n\n- Status: **ERRO**\n- {exc}\n"
        exit_code = 2
    else:
        exit_code = 1 if any(item.severity == "blocker" for item in findings) else 0

    report_path = repo_root / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(report, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
