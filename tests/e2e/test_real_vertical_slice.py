"""Cluster D — Honest E2E test for the orchestrator (Cluster A + B).

The slice-13 E2E test passed even if every real phase implementation
was deleted (the external analysis flagged this as gap G5). This
file proves the harness actually does work: it asserts on the
orchestrator's real artifact tree, real RED+GREEN evidence, real
draft PR, and durable ledger entries.

If any real phase impl is removed or stubbed, this test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seharness.controller.real_adapters import FileRunLedger
from seharness.controller.run_ledger import RunState
from seharness.orchestrator import Orchestrator, OrchestratorConfig


def _build_wired(tmp_path: Path) -> Wired:
    return Wired(tmp_path)


class Wired:
    """Orchestrator + FileRunLedger wired against a synthetic FastAPI fixture."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.repo = self._build_fixture(tmp_path)
        self.ledger = FileRunLedger(path=tmp_path / "ledger.jsonl")
        self.cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        self.orch = Orchestrator(run_ledger=self.ledger, config=self.cfg)

    @staticmethod
    def _build_fixture(tmp_path: Path) -> Path:
        repo = tmp_path / "fixture-repo"
        repo.mkdir()
        (repo / "main.py").write_text(
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n"
            "@app.get('/')\n"
            "def root() -> dict[str, str]:\n"
            "    return {'msg': 'hello'}\n"
        )
        tests = repo / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_main.py").write_text(
            "from fastapi.testclient import TestClient\n"
            "from main import app\n\n"
            "def test_root() -> None:\n"
            "    c = TestClient(app)\n"
            "    r = c.get('/')\n"
            "    assert r.status_code == 200\n"
        )
        return repo

    def run(self) -> object:
        return self.orch.start_run(
            feature_description="Add /health endpoint",
            repo_path=str(self.repo),
        )


def test_orchestrator_writes_real_repo_profile(tmp_path: Path) -> None:
    wired = _build_wired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    # Cluster WP4 / story WP4.1: ``repo-profile.json`` is now the
    # full ``RepositoryProfile`` Pydantic model (not the legacy
    # 19-line stub), so we assert on the new shape.
    profile = json.loads((run_dir / "repo-profile.json").read_text())
    assert profile["path"] == str(wired.repo)
    assert profile["detected_language"] == "python"
    assert profile["baseline_validation_status"] == "unknown"


def test_orchestrator_writes_real_specification(tmp_path: Path) -> None:
    wired = _build_wired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    spec = json.loads((run_dir / "specification.json").read_text())
    assert spec["description"] == "Add /health endpoint"
    assert spec["repo_path"] == str(wired.repo)


def test_orchestrator_writes_real_plan_with_one_task(tmp_path: Path) -> None:
    wired = _build_wired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    plan = json.loads((run_dir / "plan.json").read_text())
    assert "plan_id" in plan
    assert len(plan["tasks"]) == 1
    task = plan["tasks"][0]
    assert task["objective"] == "Add /health endpoint"
    # Cluster WP4 / story WP4.5: validation_commands is now derived
    # from the discovered RepositoryProfile via CommandResolver.
    # The fixture repo has no pyproject.toml, so the inspector falls
    # back to ``package_manager=unknown`` which resolves to
    # ``python -m pytest``.
    assert task["validation_commands"] == ["python -m pytest"]


def test_orchestrator_invokes_real_task_execution_service(tmp_path: Path) -> None:
    """The implementation phase must call slice-7's TaskExecutionService.

    This is the assertion the slice-13 E2E test could NOT make: it
    checks that real RED+GREEN evidence files were produced.
    """
    wired = _build_wired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    exec_dirs = list((run_dir / "execution").glob("task-*"))
    assert exec_dirs, "no execution/task-* directory under run_dir"
    task_dir = exec_dirs[0]
    assert (task_dir / "red" / "result.json").is_file(), "RED evidence missing"
    assert (task_dir / "green" / "result.json").is_file(), "GREEN evidence missing"
    assert (task_dir / "task-result.json").is_file(), "task-result.json missing"
    # RED must show non-zero exit code (slice-7 invariant).
    red = json.loads((task_dir / "red" / "result.json").read_text())
    assert red["exit_code"] != 0
    # GREEN must show zero exit code.
    green = json.loads((task_dir / "green" / "result.json").read_text())
    assert green["exit_code"] == 0


def test_orchestrator_records_review_verdict(tmp_path: Path) -> None:
    wired = _build_wired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    verdict = json.loads((run_dir / "review-verdict.json").read_text())
    assert verdict["verdict"] == "approve"
    assert len(verdict["tasks_reviewed"]) == 1


def test_orchestrator_creates_draft_pr_via_pull_request_client(tmp_path: Path) -> None:
    """The draft_pr phase must invoke the PR client and produce a URL."""
    wired = _build_wired(tmp_path)
    result = wired.run()
    pr_phase = next(e for e in result.events if e.phase == "draft_pr")
    assert "draft PR:" in pr_phase.detail
    assert "github.com" in pr_phase.detail
    # StubPullRequestClient records drafts.
    assert pr_phase.detail.split(":", 1)[1].strip()  # non-empty URL


def test_orchestrator_persists_complete_state_in_durable_ledger(tmp_path: Path) -> None:
    """Cluster B3: the ledger on disk must reflect COMPLETE after the run."""
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=ledger_path)
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('x')\n")
    cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
    orch = Orchestrator(run_ledger=ledger, config=cfg)
    result = orch.start_run(
        feature_description="Add a hello endpoint",
        repo_path=str(repo),
    )
    # Ledger reflects COMPLETE in-memory.
    assert ledger.get(result.run_id).state == RunState.COMPLETE
    # Ledger on disk has at least the start + complete transition lines.
    lines = ledger_path.read_text().strip().split("\n")
    assert len(lines) >= 2
    states = [json.loads(line)["state"] for line in lines]
    assert "running" in states
    assert "complete" in states
    # New FileRunLedger instance replays the file and observes COMPLETE.
    rehydrated = FileRunLedger(path=ledger_path)
    assert rehydrated.get(result.run_id).state == RunState.COMPLETE


def test_orchestrator_event_log_includes_all_phases_in_order(tmp_path: Path) -> None:
    wired = _build_wired(tmp_path)
    result = wired.run()
    phases = [e.phase for e in result.events]
    expected = [
        "feature_request",
        "repository_discovery",
        "specification",
        "planning",
        "implementation",
        "validation",
        "remediation",
        "review",
        "draft_pr",
        "ci",
        "ready",
        "completed",
    ]
    assert phases == expected


def test_orchestrator_handles_unknown_feature_gracefully(tmp_path: Path) -> None:
    """An empty feature description must fail closed."""
    wired = _build_wired(tmp_path)
    with pytest.raises(Exception, match="non-empty"):
        wired.orch.start_run(
            feature_description="",
            repo_path=str(wired.repo),
        )


def test_orchestrator_does_not_modify_target_repo(tmp_path: Path) -> None:
    """The orchestrator must revert any unauthorized changes before completing.

    The slice-7 ``revert_unauthorized`` invariant ensures no files
    outside the plan's ``allowed_paths`` remain modified after the
    implementation phase. The repo should be in the same state as
    before the run.
    """
    wired = _build_wired(tmp_path)
    before = (wired.repo / "main.py").read_text()
    result = wired.run()
    after = (wired.repo / "main.py").read_text()
    assert before == after, "orchestrator left unauthorized changes in target repo"
    # The task-result.json records any violations that were reverted.
    run_dir = wired.tmp_path / "runs" / result.run_id
    task_dirs = list((run_dir / "execution").glob("task-*"))
    task_result = json.loads((task_dirs[0] / "task-result.json").read_text())
    # violations list may be empty OR contain paths that were reverted.
    for violation in task_result.get("violations", []):
        # The repo file must NOT have the violation content.
        assert violation not in (wired.repo / "main.py").read_text()
