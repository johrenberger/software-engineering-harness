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


# ---------------------------------------------------------------------------
# E1 — FileRunLedger idempotency-key round-trip
# ---------------------------------------------------------------------------


def test_file_run_ledger_persists_idempotency_key(tmp_path: Path) -> None:
    """When ``record_start`` is called with ``idempotency_key``, the
    key is stored on the record AND on the JSONL envelope."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    rec = ledger.record_start("orch-001", repository="/tmp/repo", idempotency_key="req-abc")
    assert rec.idempotency_key == "req-abc"

    # JSONL line carries the key (to_jsonl omits empty keys).
    first_line = json.loads(path.read_text().strip().split("\n")[0])
    assert first_line.get("idempotency_key") == "req-abc"


def test_file_run_ledger_replay_preserves_idempotency_key(tmp_path: Path) -> None:
    """A fresh ``FileRunLedger`` instance replaying a file with a
    populated ``idempotency_key`` must reconstruct the key on the
    record."""
    path = tmp_path / "ledger.jsonl"
    a = FileRunLedger(path=path)
    a.record_start("orch-001", repository="/tmp/repo", idempotency_key="req-xyz")
    a.mark_complete("orch-001")
    # Replay into a fresh instance.
    b = FileRunLedger(path=path)
    rec = b.get("orch-001")
    assert rec is not None
    assert rec.idempotency_key == "req-xyz"
    assert rec.state == RunState.COMPLETE


def test_file_run_ledger_omits_empty_idempotency_key(tmp_path: Path) -> None:
    """When no key is provided, the JSONL line does NOT carry the
    ``idempotency_key`` field (keeps the on-disk format terse)."""
    path = tmp_path / "ledger.jsonl"
    ledger = FileRunLedger(path=path)
    ledger.record_start("orch-001", repository="/tmp/repo")
    first_line = json.loads(path.read_text().strip().split("\n")[0])
    assert "idempotency_key" not in first_line
    # And of course the record has an empty key.
    assert ledger.get("orch-001").idempotency_key == ""


# ---------------------------------------------------------------------------
# E2 — FileRunLedger optimistic-concurrency + revision round-trip
# ---------------------------------------------------------------------------


def _mk(tmp_path: Path) -> Path:
    return tmp_path / "ledger_e2.jsonl"


def test_file_run_ledger_revision_default_one(tmp_path: Path) -> None:
    """A fresh record starts at revision 1 in the durable ledger;
    the JSONL envelope carries the revision number so replays
    reconstruct it correctly."""
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    rec = ledger.record_start("orch-001", repository="/tmp/repo")
    assert rec.revision == 1
    first_line = json.loads(path.read_text().strip().split("\n")[0])
    assert first_line.get("revision") == 1


def test_file_run_ledger_replace_bumps_revision(tmp_path: Path) -> None:
    """``record_start`` on an existing ``run_id`` (E1 re-keying path)
    bumps revision in the durable ledger."""
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo", idempotency_key="k-A")
    ledger.record_start("r1", repository="/repo", idempotency_key="k-B")
    assert ledger.get("r1").revision == 2


def test_file_run_ledger_mark_with_revision_cas_succeeds(tmp_path: Path) -> None:
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    ledger.mark_paused("r1")
    result = ledger.mark_complete("r1", expected_revision=2)
    assert result is not None
    assert result.revision == 3
    assert result.state == RunState.COMPLETE


def test_file_run_ledger_mark_revision_cas_fails(tmp_path: Path) -> None:
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    ledger.mark_paused("r1")
    with pytest.raises(OptimisticConcurrencyError):
        ledger.mark_complete("r1", expected_revision=1)
    # Ledger state preserved.
    assert ledger.get("r1").state == RunState.PAUSED
    assert ledger.get("r1").revision == 2


def test_file_run_ledger_mark_state_cas_fails(tmp_path: Path) -> None:
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    ledger.mark_paused("r1")
    with pytest.raises(OptimisticConcurrencyError):
        ledger.mark_resume("r1", expected_state=RunState.RUNNING)
    assert ledger.get("r1").state == RunState.PAUSED


def test_file_run_ledger_replay_preserves_revision(tmp_path: Path) -> None:
    """After several writes the JSONL line for the final transition
    carries the latest revision; replaying into a fresh instance
    reconstructs the same revision number."""
    path = _mk(tmp_path)
    a = FileRunLedger(path=path)
    a.record_start("r1", repository="/repo")
    a.mark_paused("r1")
    a.mark_resume("r1")
    # Last state on disk is RUNNING at revision 3.
    b = FileRunLedger(path=path)
    rec = b.get("r1")
    assert rec is not None
    assert rec.state == RunState.RUNNING
    assert rec.revision == 3


# ---------------------------------------------------------------------------
# E3 — FileRunLedger phase + ctx + feature_description round-trip
# ---------------------------------------------------------------------------


def test_file_run_ledger_record_phase_persists_and_replays(tmp_path: Path) -> None:
    """``record_phase`` writes the new phase + ctx to the JSONL
    envelope and a fresh ``FileRunLedger`` replaying the same file
    reconstructs them."""
    path = _mk(tmp_path)
    a = FileRunLedger(path=path)
    a.record_start("r1", repository="/repo", feature_description="add auth")
    a.record_phase("r1", phase="implementation", ctx={"task_results": [{"id": 1}]})
    a.record_phase("r1", phase="validation", ctx={"exit_code": 0})
    # Spin up a fresh ledger pointed at the same file; replay should
    # see the latest phase + ctx (last-write-wins).
    b = FileRunLedger(path=path)
    rec = b.get("r1")
    assert rec is not None
    assert rec.phase == "validation"
    assert rec.ctx == {"exit_code": 0}
    assert rec.feature_description == "add auth"
    assert rec.revision == 3


def test_file_run_ledger_phase_none_omitted_from_jsonl(tmp_path: Path) -> None:
    """When ``phase`` is None the JSONL line MUST omit it (matches
    the E1 idempotency_key style — keeps the format terse for
    callers that haven't wired E3 yet).
    """
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    line = json.loads(path.read_text().strip().split("\n")[0])
    assert "phase" not in line
    assert "ctx" not in line
    assert "feature_description" not in line


def test_file_run_ledger_phase_present_in_jsonl(tmp_path: Path) -> None:
    """When ``record_phase`` fires, the phase + ctx are written to
    the JSONL envelope. ``record_phase`` lines are written via
    ``_update_state`` so they appear as ``kind='transition'``.
    """
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    ledger.record_phase("r1", phase="specification", ctx={"spec_id": 7})
    lines = [json.loads(line) for line in path.read_text().strip().split("\n")]
    assert len(lines) == 2
    assert lines[1]["kind"] == "transition"
    assert lines[1]["phase"] == "specification"
    assert lines[1]["ctx"] == {"spec_id": 7}


def test_file_run_ledger_replay_preserves_phase_across_restarts(tmp_path: Path) -> None:
    """Simulates a process restart: write some phases, close the
    ledger, reopen it, confirm the cursor survives.
    """
    path = _mk(tmp_path)
    a = FileRunLedger(path=path)
    a.record_start("r1", repository="/repo", feature_description="feat")
    a.record_phase("r1", phase="implementation", ctx={"x": 1})
    a.record_phase("r1", phase="validation", ctx={"x": 2})
    # ``del a`` simulates the process exiting; the file on disk
    # is the only state.
    del a
    b = FileRunLedger(path=path)
    rec = b.get("r1")
    assert rec is not None
    assert rec.phase == "validation"
    assert rec.ctx == {"x": 2}


def test_file_run_ledger_record_phase_empty_phase_raises(tmp_path: Path) -> None:
    """Defensive: empty phase is rejected before touching the file."""
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    with pytest.raises(ValueError, match="phase"):
        ledger.record_phase("r1", phase="")


def test_file_run_ledger_record_phase_non_dict_ctx_raises(tmp_path: Path) -> None:
    """``ctx`` must be a dict (or None) so the on-disk format
    stays predictable.
    """
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="/repo")
    with pytest.raises(ValueError, match="ctx"):
        ledger.record_phase("r1", phase="implementation", ctx=[1, 2, 3])


# ---------------------------------------------------------------------------
# Cluster P3: cost-attribution on FileRunLedger (JSONL roundtrip)
# ---------------------------------------------------------------------------


def test_p3_file_run_ledger_record_cost_attribution_stamps_totals(tmp_path: Path) -> None:
    """Cluster P3: stamping cost-attribution on the file
    ledger appends a transition line carrying the four
    fields; a replay reconstructs them on the in-memory
    index.
    """
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="repo")
    rec = ledger.record_cost_attribution(
        "r1",
        total_tokens=2500,
        total_cost_usd=0.012,
        total_elapsed_s=4.5,
    )
    assert rec is not None
    assert rec.total_tokens == 2500
    assert rec.total_cost_usd == 0.012
    assert rec.total_elapsed_s == 4.5
    assert rec.by_task is None
    # State preserved through the cost-attribution stamp.
    assert rec.state == RunState.RUNNING

    # Replay and verify the JSONL envelope roundtrip.
    replayed = FileRunLedger(path=path).get("r1")
    assert replayed is not None
    assert replayed.total_tokens == 2500
    assert replayed.total_cost_usd == 0.012
    assert replayed.total_elapsed_s == 4.5


def test_p3_file_run_ledger_by_task_roundtrip(tmp_path: Path) -> None:
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="repo")
    by_task = {
        "task-foo": {"model_tokens": 100.0, "model_cost_usd": 0.003},
        "task-bar": {"model_tokens": 250.0, "model_cost_usd": 0.007},
    }
    ledger.record_cost_attribution("r1", by_task=by_task)
    replayed = FileRunLedger(path=path).get("r1")
    assert replayed is not None
    assert replayed.by_task == by_task


def test_p3_file_run_ledger_omits_none_fields_from_jsonl(tmp_path: Path) -> None:
    """Cluster P3 envelope shape: pre-P3 lines that lack the
    cost fields must still load. Verify by writing a JSONL
    line without the fields and replaying it.
    """
    path = _mk(tmp_path)
    # Hand-craft a JSONL line that pre-dates P3.
    payload = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.total_tokens is None
    assert rec.total_cost_usd is None
    assert rec.total_elapsed_s is None
    assert rec.by_task is None


def test_p3_file_run_ledger_replay_preserves_partial_cost_attribution(tmp_path: Path) -> None:
    """Cluster P3: a transition line carrying only some of
    the cost fields preserves the others (mirrors the in-
    memory carry-forward).
    """
    path = _mk(tmp_path)
    # Hand-craft a sequence: start with no cost attribution,
    # transition with only total_tokens set.
    start = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
    }
    transition = {
        "kind": "transition",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:01:00+00:00",
        "revision": 2,
        "total_tokens": 500,
    }
    path.write_text(
        json.dumps(start) + "\n" + json.dumps(transition) + "\n",
        encoding="utf-8",
    )
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.total_tokens == 500
    # Others preserved as ``None`` (the start line's absence).
    assert rec.total_cost_usd is None
    assert rec.total_elapsed_s is None
    assert rec.by_task is None


def test_p3_file_run_ledger_rejects_negative_cost_attribution(tmp_path: Path) -> None:
    """Negative cost-attribution is rejected at the Pydantic
    layer (Cluster P3 invariant). A corrupt line on disk is
    loaded as ``None`` via the _coerce helpers instead of
    crashing the replay.
    """
    path = _mk(tmp_path)
    payload = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
        "total_tokens": -10,
        "total_cost_usd": -0.01,
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    # Negative values on disk load as ``None`` (the _coerce
    # helpers reject them) so a corrupt line doesn't crash
    # replay.
    assert rec.total_tokens is None
    assert rec.total_cost_usd is None


def test_p3_file_run_ledger_cost_attribution_cas_stale_revision_raises(tmp_path: Path) -> None:
    """Cluster E2: stale ``expected_revision`` on
    ``record_cost_attribution`` raises BEFORE mutation.
    """
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    ledger.record_start("r1", repository="repo")  # revision 1
    ledger.record_phase("r1", phase="spec")  # revision 2
    with pytest.raises(OptimisticConcurrencyError):
        ledger.record_cost_attribution(
            "r1",
            total_tokens=100,
            expected_revision=1,
        )
    # Ledger untouched.
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.revision == 2
    assert rec.total_tokens is None


def test_p3_file_run_ledger_coerce_helpers_reject_bool_and_garbage(tmp_path: Path) -> None:
    """Defensive coverage: the _coerce_optional_int /
    _coerce_optional_float / _coerce_by_task helpers reject
    booleans, non-numeric values, and malformed by_task
    payloads so a corrupt JSONL line doesn't crash replay.
    """
    path = _mk(tmp_path)
    payload = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
        "total_tokens": True,  # bool rejected
        "total_cost_usd": "not-a-number",  # non-numeric rejected
        "total_elapsed_s": True,  # bool rejected
        "by_task": "not-a-dict",  # non-dict rejected
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    # All four cost-attribution fields fall back to ``None``.
    assert rec.total_tokens is None
    assert rec.total_cost_usd is None
    assert rec.total_elapsed_s is None
    assert rec.by_task is None


def test_p3_file_run_ledger_cost_attribution_unknown_run_returns_none(tmp_path: Path) -> None:
    """Unknown ``run_id`` returns ``None`` (mirrors ``mark_*``)."""
    path = _mk(tmp_path)
    ledger = FileRunLedger(path=path)
    assert ledger.record_cost_attribution("ghost", total_tokens=1) is None


def test_p3_file_run_ledger_by_task_partial_corruption_drops_bad_entries(tmp_path: Path) -> None:
    """A ``by_task`` payload with mixed valid + invalid
    entries preserves the valid ones and drops the invalid
    ones (so a single bad entry can't poison the view).
    """
    path = _mk(tmp_path)
    payload = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
        "by_task": {
            "task-good": {"model_tokens": 100.0, "model_cost_usd": 0.003},
            "task-bad-inner": {"model_tokens": True, "model_cost_usd": "garbage"},
            "task-bad-axes": [1, 2, 3],
            "": {"model_tokens": 50.0},  # empty task_id dropped
        },
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.by_task is not None
    # Good task preserved with all axes.
    assert rec.by_task["task-good"] == {
        "model_tokens": 100.0,
        "model_cost_usd": 0.003,
    }
    # Bad-axes task (inner is a list) is dropped entirely.
    assert "task-bad-axes" not in rec.by_task
    # Bad-inner task survives but its bad inner values are
    # dropped, leaving an empty inner mapping.
    assert rec.by_task["task-bad-inner"] == {}
    # Empty task_id is dropped.
    assert "" not in rec.by_task


def test_p3_file_run_ledger_by_task_drops_empty_axis(tmp_path: Path) -> None:
    """Defensive: an empty axis key in ``by_task`` is dropped
    (a non-empty string axis is preserved even if it looks
    unusual; only empty keys are filtered). Covers the
    ``not axis`` branch in the inner coercion loop.
    """
    path = _mk(tmp_path)
    payload = {
        "kind": "start",
        "run_id": "r1",
        "state": "running",
        "repository": "repo",
        "timestamp": "2026-07-22T03:00:00+00:00",
        "revision": 1,
        "by_task": {
            "task-x": {
                "model_tokens": 100.0,
                "": 0.25,  # empty axis key dropped
            },
        },
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    ledger = FileRunLedger(path=path)
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.by_task == {
        "task-x": {"model_tokens": 100.0},
    }
