from __future__ import annotations

import tomllib
from pathlib import Path


def _dependency_groups() -> dict[str, list[str]]:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    project = pyproject["project"]
    optional = project["optional-dependencies"]
    return {
        "runtime": project["dependencies"],
        "development": optional["dev"],
        "audit": optional["audit"],
    }


def test_all_direct_application_dependencies_are_exactly_pinned() -> None:
    unpinned = {
        group: [dependency for dependency in dependencies if "==" not in dependency]
        for group, dependencies in _dependency_groups().items()
    }

    assert unpinned == {
        "runtime": [],
        "development": [],
        "audit": [],
    }


def test_dependency_update_automation_is_configured() -> None:
    dependabot = Path(".github/dependabot.yml").read_text()

    assert "package-ecosystem: pip" in dependabot
    assert "package-ecosystem: github-actions" in dependabot
    assert "timezone: America/Fortaleza" in dependabot
