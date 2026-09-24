import re
from pathlib import Path

CI_WORKFLOW = Path(".github/workflows/ci.yml")


def test_main_push_sync_promotes_langfuse_production() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "push:\n    branches: [main]" in workflow
    assert "needs: quality" in workflow
    assert "run: python scripts/bootstrap_langfuse_prompts.py --promote-production" in workflow


def test_quality_workflow_runs_diff_gate_with_full_history_and_publishes_reports() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "QUALITY_BASE_REF:" in workflow
    assert "github.event.pull_request.base.sha" in workflow
    assert "github.event.before" in workflow
    assert "fetch-depth: 0" in workflow
    assert "make quality PYTHON=python RUFF=ruff PYTEST=pytest" in workflow
    assert "build/quality/python-structure.md" in workflow
    assert "build/quality/diff-coverage.md" in workflow
    assert workflow.count("if: always()") >= 2
    assert re.search(
        r"uses: actions/upload-artifact@[0-9a-f]{40} # v4",
        workflow,
    )
