from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path


def _import_targets(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    targets: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)

    return targets


def _python_files(root: str) -> list[Path]:
    return sorted(path for path in Path(root).rglob("*.py") if "__pycache__" not in path.parts)


def test_route_modules_do_not_import_agent_or_integrations_directly() -> None:
    route_files = sorted(Path("src/app/api/routes").glob("*.py"))

    for path in route_files:
        targets = _import_targets(path)
        assert all(not target.startswith("app.agent") for target in targets), path
        assert all(not target.startswith("app.integrations") for target in targets), path


def test_agent_modules_do_not_import_pipefacil() -> None:
    agent_files = _python_files("src/app/agent")

    for path in agent_files:
        targets = _import_targets(path)
        assert all(not target.startswith("app.integrations.pipefacil") for target in targets), path


def test_agent_nodes_and_tools_do_not_import_httpx_directly() -> None:
    agent_files = [
        *_python_files("src/app/agent/nodes"),
        *_python_files("src/app/agent/tools"),
    ]

    for path in agent_files:
        targets = _import_targets(path)
        assert "httpx" not in targets, path


def test_langgraph_config_keeps_agent_studio_export_path() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["graphs"]["sdr_pipefacil"] == "./src/app/agent/agent.py:graph"


def test_development_mcp_configs_point_to_docs_servers() -> None:
    expected_mcp_servers = {
        "docs-langchain": "https://docs.langchain.com/mcp",
        "langfuse-docs": "https://langfuse.com/api/mcp",
    }

    root_config = json.loads(Path(".mcp.json").read_text())
    cursor_config = json.loads(Path(".cursor/mcp.json").read_text())
    vscode_config = json.loads(Path(".vscode/mcp.json").read_text())

    for server_name, url in expected_mcp_servers.items():
        assert root_config["mcpServers"][server_name]["url"] == url
        assert cursor_config["mcpServers"][server_name]["url"] == url
        assert vscode_config["servers"][server_name]["url"] == url


def test_agent_context_references_current_langfuse_docs() -> None:
    agent_context = Path("AGENTS.md").read_text()

    assert "https://langfuse.com/llms.txt" in agent_context
    assert "https://langfuse.com/api/mcp" in agent_context
    assert "docs/observabilidade-langfuse.md" in agent_context


def test_documentation_mcp_context_stays_out_of_production_runtime() -> None:
    project_config = tomllib.loads(Path("pyproject.toml").read_text())
    runtime_dependencies = project_config["project"]["dependencies"]
    dockerfile = Path("Dockerfile").read_text()

    assert all("mcp" not in dependency.lower() for dependency in runtime_dependencies)
    assert '".[dev]"' not in dockerfile
