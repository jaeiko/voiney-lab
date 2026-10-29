"""HEAR2: branch pushes do not duplicate the pull-request CI run."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_ci_push_and_pull_request_branches_are_the_intended_two() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    push = section(workflow, "  push:\n", "  pull_request:\n")
    pull_request = section(workflow, "  pull_request:\n", "\njobs:\n")

    assert 'branches: ["main", "dev"]' in push
    assert "feature/**" not in push
    assert "fix/**" not in push
    assert "refactor/**" not in push
    assert 'branches: ["main", "dev"]' in pull_request


def test_staging_still_requires_a_successful_dev_push_ci_run() -> None:
    workflow = (ROOT / ".github/workflows/deploy-staging.yml").read_text(
        encoding="utf-8"
    )
    trigger = section(workflow, "  workflow_run:\n", "\nconcurrency:\n")

    assert "workflows: [\"CI\"]" in trigger
    assert "branches: [dev]" in trigger
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
