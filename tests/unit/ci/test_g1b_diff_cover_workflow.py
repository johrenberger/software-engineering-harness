"""RED tests for G1b CI workflow.

Cluster G story G1b: per-PR coverage delta via diff-cover. The CI
workflow must:
  1. fetch the full git history (fetch-depth: 0) so diff-cover can
     compute the diff vs origin/main.
  2. Run ``diff-cover`` on ``coverage.xml`` with --fail-under=80 and
     --src-roots=src on pull_request events only.
  3. Publish per-file coverage + the diff-cover markdown to the GH
     Actions job summary.
  4. Stay valid YAML (parseable by PyYAML).

These tests guard against silent regressions in the workflow file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = Path(".github/workflows/ci.yml")
DIFF_COVER_FLOOR = 80


@pytest.fixture(scope="module")
def workflow() -> dict[str, object]:
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def test_workflow_file_exists() -> None:
    assert WORKFLOW_PATH.is_file(), f"{WORKFLOW_PATH} missing"


def test_workflow_parses_as_yaml() -> None:
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert isinstance(data, dict)


def test_checkout_uses_full_history() -> None:
    """diff-cover needs the full history to diff against origin/main."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    checkout = next(s for s in steps if "checkout" in str(s.get("uses", "")))
    with_ = checkout.get("with", {})
    assert with_.get("fetch-depth") == 0, (
        "checkout must use fetch-depth: 0 so diff-cover can diff vs origin/main"
    )


def test_pytest_runs_without_no_cov() -> None:
    """G1a already removed --no-cov; G1b must not reintroduce it."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    pytest_step = next(s for s in steps if s.get("name") == "pytest")
    run = str(pytest_step.get("run", ""))
    assert "--no-cov" not in run, "G1a removed --no-cov; G1b must keep it removed"


def test_diff_cover_step_present() -> None:
    """G1b: a step named coverage-diff-check must invoke diff-cover."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step_names = [s.get("name") for s in steps]
    assert "coverage-diff-check" in step_names


def test_diff_cover_uses_correct_args() -> None:
    """diff-cover must: compare against origin/main, scope to src/, fail under 80."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step = next(s for s in steps if s.get("name") == "coverage-diff-check")
    run = str(step.get("run", ""))
    assert "diff-cover coverage.xml" in run
    assert "--compare-branch=origin/main" in run
    assert "--src-roots=src" in run
    assert f"--fail-under={DIFF_COVER_FLOOR}" in run


def test_diff_cover_only_runs_on_pull_request() -> None:
    """diff-cover makes no sense for push-to-main (no diff to compare)."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step = next(s for s in steps if s.get("name") == "coverage-diff-check")
    assert step.get("if") == "github.event_name == 'pull_request'"


def test_diff_cover_emits_markdown_report() -> None:
    """The diff-cover markdown report feeds the GH Actions job summary."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step = next(s for s in steps if s.get("name") == "coverage-diff-check")
    run = str(step.get("run", ""))
    assert "--markdown-report=/tmp/diff-cover.md" in run


def test_coverage_summary_step_present() -> None:
    """Job summary step must exist so reviewers see coverage inline."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step_names = [s.get("name") for s in steps]
    assert "coverage-summary" in step_names


def test_coverage_summary_writes_to_github_step_summary() -> None:
    """The summary step must append to $GITHUB_STEP_SUMMARY, not just print."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step = next(s for s in steps if s.get("name") == "coverage-summary")
    run = str(step.get("run", ""))
    assert "GITHUB_STEP_SUMMARY" in run
    assert "coverage report" in run


def test_diff_summary_step_present() -> None:
    """On PRs, the diff-cover markdown must be appended to the summary."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    step_names = [s.get("name") for s in steps]
    assert "coverage-diff-summary" in step_names


def test_pytest_before_diff_cover() -> None:
    """diff-cover reads coverage.xml produced by pytest; pytest must run first."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    names = [s.get("name") for s in steps]
    pytest_idx = names.index("pytest")
    diff_idx = names.index("coverage-diff-check")
    assert pytest_idx < diff_idx, "coverage-diff-check must run after pytest"


def test_workflow_preserves_pre_g1b_steps() -> None:
    """G1b adds steps; it must not remove the existing gates."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    job = data["jobs"]["quality-gate"]  # type: ignore[index]
    steps = job["steps"]  # type: ignore[index]
    names = {s.get("name") for s in steps}
    expected = {
        "ruff format check",
        "ruff check",
        "mypy strict",
        "bandit",
        "pip-audit",
        "pytest",
    }
    missing = expected - names
    assert not missing, f"G1b removed pre-existing steps: {missing}"


def test_dev_extra_includes_diff_cover() -> None:
    """pyproject.toml must declare diff-cover in [dev] so CI installs it."""
    import tomllib

    data = tomllib.loads(Path("pyproject.toml").read_text())
    dev = data["project"]["optional-dependencies"]["dev"]
    assert any(p.startswith("diff-cover") for p in dev), "diff-cover must be a [dev] dependency"
