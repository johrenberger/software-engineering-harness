"""Unit tests for the production adapters (Cluster B).

Covers:
- B1: ``LocalTaskExecutor`` — wraps slice-7 ``TaskExecutionService``,
  produces a real task_result, satisfies the ``FeatureExecutor``
  Protocol via ``execute/resume/cancel``.
- B2: ``GitHubChecksClient`` — fails closed (``AdapterUnavailable``)
  when ``gh`` is missing or ``GITHUB_TOKEN`` is unset; produces a
  valid ``RequiredChecksView`` shape when the API responds.
- B3: ``FileRunLedger`` — appends JSONL lines, replays them on
  startup, truncates partial last lines, bounds records to
  ``max_records``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    RequiredChecksView,
)
from seharness.controller.real_adapters import (
    AdapterUnavailable,
    FileRunLedger,
    GitHubChecksClient,
    LocalTaskExecutor,
)
from seharness.controller.run_ledger import RunState
from seharness.telegram.service import FeatureRequest

# ---------------------------------------------------------------------------
# B1 — LocalTaskExecutor
# ---------------------------------------------------------------------------


def _build_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def x() -> int:\n    return 1\n")
    return repo


def test_local_task_executor_produces_real_task_result(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    exec_root = tmp_path / "runs"
    executor = LocalTaskExecutor(repo_root=repo, execution_root=exec_root)
    req = FeatureRequest(description="Add a hello endpoint", repository_url=str(repo))
    result = executor.execute(req)
    assert result["ok"] is True
    task_id = result["task_id"]
    assert task_id.startswith("task-")
    # TaskExecutionService writes task-result.json somewhere under exec_root.
    candidates = list(exec_root.rglob("task-result.json"))
    assert candidates, "no task-result.json produced"


def test_local_task_executor_resume_returns_ok(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    executor = LocalTaskExecutor(repo_root=repo, execution_root=tmp_path / "runs")
    result = executor.resume("orch-abc12345")
    assert result["ok"] is True
    assert result["run_id"] == "orch-abc12345"


def test_local_task_executor_cancel_returns_ok(tmp_path: Path) -> None:
    repo = _build_repo(tmp_path)
    executor = LocalTaskExecutor(repo_root=repo, execution_root=tmp_path / "runs")
    result = executor.cancel("orch-abc12345")
    assert result["ok"] is True
    assert result["status"] == "cancelled"


def test_local_task_executor_satisfies_feature_executor_protocol(tmp_path: Path) -> None:
    """Mutant killer: must have execute/resume/cancel methods."""
    from seharness.controller.application_service import FeatureExecutor

    repo = _build_repo(tmp_path)
    executor = LocalTaskExecutor(repo_root=repo, execution_root=tmp_path / "runs")
    # Structural protocol conformance: if methods exist, the
    # Protocol's `execute(resume/cancel)` shape is satisfied.
    assert hasattr(executor, "execute")
    assert hasattr(executor, "resume")
    assert hasattr(executor, "cancel")
    assert FeatureExecutor.__call__ is not None  # type: ignore[attr-defined]


def test_local_task_executor_has_no_merge_method(tmp_path: Path) -> None:
    """Auto-merge prevention layer 7: real adapter exposes no merge*."""
    repo = _build_repo(tmp_path)
    executor = LocalTaskExecutor(repo_root=repo, execution_root=tmp_path / "runs")
    forbidden = {"merge", "merge_pull_request", "auto_merge", "merge_pr", "gh_merge"}
    for name in dir(executor):
        if name.startswith("_"):
            continue
        assert name not in forbidden, f"LocalTaskExecutor exposes {name}"


# ---------------------------------------------------------------------------
# B2 — GitHubChecksClient
# ---------------------------------------------------------------------------


def test_github_checks_client_unavailable_without_gh(tmp_path: Path) -> None:
    client = GitHubChecksClient(repo="x/y")
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(AdapterUnavailable, match="gh CLI"),
    ):
        client.fetch_view("42", "main")


def test_github_checks_client_unavailable_without_token(tmp_path: Path) -> None:
    client = GitHubChecksClient(repo="x/y")
    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(AdapterUnavailable, match="GITHUB_TOKEN"),
    ):
        client.fetch_view("42", "main")


def test_github_checks_client_translates_api_response(tmp_path: Path) -> None:
    """Successful gh api call must produce a valid RequiredChecksView."""
    client = GitHubChecksClient(repo="x/y")
    pr_payload = {"head": {"sha": "abc123"}}
    fake_response = {
        "check_runs": [
            {
                "name": "ci/test",
                "status": "completed",
                "conclusion": "success",
                "required": True,
            },
            {
                "name": "ci/lint",
                "status": "in_progress",
                "conclusion": None,
                "required": False,
            },
        ]
    }

    responses = iter(
        [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(pr_payload), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(fake_response), stderr=""
            ),
        ]
    )

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return next(responses)

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch.dict(os.environ, {"GITHUB_TOKEN": "test"}),
        patch("seharness.controller.real_adapters.subprocess.run", fake_run),
    ):
        view = client.fetch_view("42", "main")
    assert isinstance(view, RequiredChecksView)
    assert view.head_sha == "abc123"
    assert view.branch == "main"
    assert len(view.all_checks) == 2
    assert "ci/test" in view.required
    test_check = view.all_checks[0]
    assert test_check.state == CheckRunState.COMPLETED
    assert test_check.conclusion == CheckConclusion.SUCCESS


def test_github_checks_client_handles_non_json_response() -> None:
    client = GitHubChecksClient(repo="x/y")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="<html>not json</html>", stderr=""
        )

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch.dict(os.environ, {"GITHUB_TOKEN": "test"}),
        patch("seharness.controller.real_adapters.subprocess.run", fake_run),
        pytest.raises(AdapterUnavailable, match="non-JSON"),
    ):
        client.fetch_view("42", "main")


def test_github_checks_client_handles_nonzero_exit() -> None:
    client = GitHubChecksClient(repo="x/y")

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="401 Unauthorized",
        )

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch.dict(os.environ, {"GITHUB_TOKEN": "test"}),
        patch("seharness.controller.real_adapters.subprocess.run", fake_run),
        pytest.raises(AdapterUnavailable, match="exit 1"),
    ):
        client.fetch_view("42", "main")


def test_github_checks_client_handles_unknown_enum_values() -> None:
    """Mutant killer: unknown enum values must not crash; fallback to safe defaults."""
    client = GitHubChecksClient(repo="x/y")
    pr_payload = {"head": {"sha": "abc123"}}
    fake_response = {
        "check_runs": [
            {
                "name": "future_check",
                "status": "unknown_future_state",
                "conclusion": "future_conclusion",
                "required": False,
            }
        ]
    }

    responses = iter(
        [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(pr_payload), stderr=""
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(fake_response), stderr=""
            ),
        ]
    )

    def fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        return next(responses)

    with (
        patch("shutil.which", return_value="/usr/bin/gh"),
        patch.dict(os.environ, {"GITHUB_TOKEN": "test"}),
        patch("seharness.controller.real_adapters.subprocess.run", fake_run),
    ):
        view = client.fetch_view("42", "main")
    assert view.all_checks[0].state == CheckRunState.QUEUED  # fallback
    assert view.all_checks[0].conclusion is None  # unknown conclusion discarded


# ---------------------------------------------------------------------------
# B3 — FileRunLedger
# ---------------------------------------------------------------------------


def test_file_run_ledger_records_start(tmp_path: Path) -> None:
    ledger = FileRunLedger(path=tmp_path / "ledger.jsonl")
    rec = ledger.record_start("orch-001", repository="/tmp/repo")
    assert rec.state == RunState.RUNNING
    assert rec.repository == "/tmp/repo"
    assert ledger.get("orch-001") is not None


def test_file_run_ledger_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    a = FileRunLedger(path=path)
    a.record_start("orch-001", repository="/tmp/repo")
    a.mark_complete("orch-001")
    # New instance must replay the file and see the COMPLETE state.
    b = FileRunLedger(path=path)
    rec = b.get("orch-001")
    assert rec is not None
    assert rec.state == RunState.COMPLETE


def test_file_run_ledger_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    ledger.record_start("orch-001", repository="/tmp/repo")
    ledger.mark_failed("orch-001")
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "start"
    assert first["state"] == "running"
    second = json.loads(lines[1])
    assert second["kind"] == "transition"
    assert second["state"] == "failed"


def test_file_run_ledger_truncates_partial_last_line(tmp_path: Path) -> None:
    """Crash mid-write must not corrupt prior records."""
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        json.dumps(
            {
                "kind": "start",
                "run_id": "orch-001",
                "state": "running",
                "repository": "/tmp",
                "timestamp": "2026-07-19T00:00:00+00:00",
            }
        )
        + "\n"
        # Partial last line — incomplete JSON.
        + '{"kind": "transition", "run_id": "orch-001", "state":'
    )
    ledger = FileRunLedger(path=path)
    # Replay must truncate the partial line and still expose the start record.
    assert ledger.get("orch-001") is not None
    assert ledger.get("orch-001").state == RunState.RUNNING


def test_file_run_ledger_mark_all_terminal_states(tmp_path: Path) -> None:
    """Mutant killer: each terminal-state transition must persist."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    ledger.record_start("orch-001", repository="/tmp")
    assert ledger.mark_complete("orch-001").state == RunState.COMPLETE
    ledger.record_start("orch-002", repository="/tmp")
    assert ledger.mark_failed("orch-002").state == RunState.FAILED
    ledger.record_start("orch-003", repository="/tmp")
    assert ledger.mark_paused("orch-003").state == RunState.PAUSED
    ledger.record_start("orch-004", repository="/tmp")
    assert ledger.mark_blocked("orch-004").state == RunState.BLOCKED
    ledger.record_start("orch-005", repository="/tmp")
    assert ledger.mark_cancelled("orch-005").state == RunState.CANCELLED


def test_file_run_ledger_runs_returns_sorted_by_started_at(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    ledger.record_start("orch-old", repository="/tmp")
    ledger.record_start("orch-new", repository="/tmp")
    runs = ledger.runs()
    # Newest first (slice-12 invariant).
    assert runs[0].run_id == "orch-new"
    assert runs[1].run_id == "orch-old"


def test_file_run_ledger_bounds_to_max_records(tmp_path: Path) -> None:
    """Mutant killer: FIFO eviction must respect max_records."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path, max_records=3)
    for i in range(5):
        ledger.record_start(f"orch-{i:03d}", repository="/tmp")
    runs = ledger.runs()
    assert len(runs) == 3
    assert runs[0].run_id == "orch-004"


def test_file_run_ledger_update_unknown_run_returns_none(tmp_path: Path) -> None:
    """Mutant killer: must not crash on unknown run_id."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    assert ledger.mark_complete("does-not-exist") is None


def test_file_run_ledger_contains_dunder(tmp_path: Path) -> None:
    """Mutant killer: ``run_id in ledger`` must work."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    ledger.record_start("orch-001", repository="/tmp")
    assert "orch-001" in ledger
    assert "orch-002" not in ledger
    # Non-string check must not crash.
    assert (123 in ledger) is False
