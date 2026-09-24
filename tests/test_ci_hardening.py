from __future__ import annotations

import re
from pathlib import Path


def _ci_workflow() -> str:
    return Path(".github/workflows/ci.yml").read_text()


def test_ci_actions_are_pinned_to_immutable_commits() -> None:
    workflow = _ci_workflow()
    action_references = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)

    assert action_references
    assert all(re.search(r"@[0-9a-f]{40}$", reference) for reference in action_references)


def test_ci_audits_dependencies_and_production_container() -> None:
    workflow = _ci_workflow()

    assert "security:" in workflow
    assert "python -m pip check" in workflow
    assert "pip-audit --local --progress-spinner off" in workflow
    assert "container:" in workflow
    assert "docker build --pull=false --tag sdr-pipefacil:ci ." in workflow
    assert 'test "$(id -u)" -ne 0' in workflow
    assert "test -w /app/.runtime/generated-audio" in workflow


def test_prompt_promotion_waits_for_all_required_gates() -> None:
    workflow = _ci_workflow()

    assert "needs: [quality, security, container]" in workflow
    assert "python -m pip install --upgrade pip" not in workflow
