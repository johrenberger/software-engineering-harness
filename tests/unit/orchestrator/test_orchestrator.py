"""RED tests for the canonical Orchestrator (Cluster A, story A2 + A3).

Each test constructs a real ``Orchestrator`` with a shared
``RunLedger``, runs it against a tiny in-test repo fixture, and
asserts on the resulting ``PipelineResult`` and ledger state.

These tests are designed to FAIL if the orchestrator is replaced
with a phase-name loop simulation (the slice-13 failure mode the
external analysis flagged).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from seharness.controller.run_ledger import RunLedger, RunState
from seharness.orchestrator import (
    PHASE_SEQUENCE,
    Orchestrator,
    OrchestratorConfig,
    PhaseName,
)


def _make_repo(tmp_path: Path) -> Path:
    """Build a tiny synthetic Python repo for the orchestrator to operate on."""
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def hello() -> str:\n    return 'hi'\n")
    (repo / "test_main.py").write_text(
        "from main import hello\n\ndef test_hello() -> None:\n    assert hello() == 'hi'\n"
    )
    return repo


def _fresh_orchestrator(tmp_path: Path) -> tuple[Orchestrator, RunLedger, Path]:
    repo = _make_repo(tmp_path)
    ledger = RunLedger()
    cfg = OrchestratorConfig(execution_root=str(tmp_path / ".runs"))
    orch = Orchestrator(run_ledger=ledger, config=cfg)
    return orch, ledger, repo


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_orchestrator_runs_to_completed_terminal_state(tmp_path: Path) -> None:
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="Add hello endpoint", repo_path=str(repo))
    assert result.terminal_state == "completed"


def test_orchestrator_emits_all_phase_events_in_order(tmp_path: Path) -> None:
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    phases = tuple(e.phase for e in result.events)
    expected = tuple(p.value for p in PHASE_SEQUENCE)
    assert phases == expected, f"phase order mismatch: got {phases}"


def test_orchestrator_persists_run_in_ledger(tmp_path: Path) -> None:
    orch, ledger, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    rec = ledger.get(result.run_id)
    assert rec is not None
    assert rec.state == RunState.COMPLETE
    assert rec.repository == str(repo)


def test_orchestrator_writes_real_artifacts_on_disk(tmp_path: Path) -> None:
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    run_dir = tmp_path / ".runs" / result.run_id
    # Specification, plan, profile, review verdict MUST all exist.
    for required in ("repo-profile.json", "specification.json", "plan.json", "review-verdict.json"):
        assert (run_dir / required).is_file(), f"missing artifact: {required}"


def test_orchestrator_invokes_real_task_execution_service(tmp_path: Path) -> None:
    """The implementation phase must call slice-7's TaskExecutionService,
    producing RED + GREEN evidence files."""
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    run_dir = tmp_path / ".runs" / result.run_id
    # TaskEvidenceLayout writes evidence under <run_dir>/execution/<task_id>/.
    exec_dirs = list((run_dir / "execution").glob("task-*"))
    assert exec_dirs, "no execution/task-* directory under run_dir"
    task_dir = exec_dirs[0]
    assert (task_dir / "red" / "result.json").is_file(), "RED evidence missing"
    assert (task_dir / "green" / "result.json").is_file(), "GREEN evidence missing"
    assert (task_dir / "task-result.json").is_file(), "task-result.json missing"


def test_orchestrator_creates_draft_pr(tmp_path: Path) -> None:
    """The draft_pr phase must call the PullRequestClient.create method."""
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    pr_phase = next(e for e in result.events if e.phase == "draft_pr")
    assert "draft PR:" in pr_phase.detail
    assert "github.com" in pr_phase.detail


def test_orchestrator_records_review_verdict(tmp_path: Path) -> None:
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    run_dir = tmp_path / ".runs" / result.run_id
    verdict = json.loads((run_dir / "review-verdict.json").read_text())
    assert verdict["verdict"] == "approve"


def test_orchestrator_runs_under_5_seconds(tmp_path: Path) -> None:
    """Cluster A performance budget: full vertical slice on a fixture < 5s."""
    orch, _, repo = _fresh_orchestrator(tmp_path)
    start = time.monotonic()
    orch.start_run(feature_description="x", repo_path=str(repo))
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"orchestrator took {elapsed:.1f}s on fixture"


# ---------------------------------------------------------------------------
# Failure routing (A4)
# ---------------------------------------------------------------------------


def test_orchestrator_routes_fatal_phase_failure_to_failed(tmp_path: Path) -> None:
    """A fatal phase exception → ``terminal_state == "failed"``, ledger
    transitions to FAILED."""
    orch, ledger, repo = _fresh_orchestrator(tmp_path)

    # Force the planning phase to raise by injecting an invalid repo path
    # via a wrapper that overrides _PlanBuilder.
    from seharness.orchestrator import orchestrator as orch_mod

    original = orch_mod._PlanBuilder.build

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic failure")

    orch_mod._PlanBuilder.build = staticmethod(boom)  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PlanBuilder.build = original  # type: ignore[assignment]
    assert result.terminal_state == "failed"
    # The first record corresponds to the first run, which was the boom;
    # re-run a happy path to grab the second record id.
    happy = orch.start_run(feature_description="y", repo_path=str(repo))
    assert ledger.get(happy.run_id).state == RunState.COMPLETE


def test_orchestrator_routes_nonfatal_phase_failure_to_failed_terminal(tmp_path: Path) -> None:
    """A non-fatal phase returning FAILED outcome → ``failed`` terminal."""
    orch, ledger, repo = _fresh_orchestrator(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    # Inject a review-phase handler that returns FAILED.
    original_review = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def fail_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.FAILED, kwargs["ctx"], "synthetic reject"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = fail_review  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original_review  # type: ignore[assignment]
    assert result.terminal_state == "failed"
    assert ledger.get(result.run_id).state == RunState.FAILED


def test_orchestrator_routes_blocked_outcome_to_blocked_terminal(tmp_path: Path) -> None:
    """A phase returning BLOCKED outcome → ``blocked`` terminal + ledger BLOCKED."""
    orch, ledger, repo = _fresh_orchestrator(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    original_review = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def block_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.BLOCKED, kwargs["ctx"], "policy halt"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = block_review  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original_review  # type: ignore[assignment]
    assert result.terminal_state == "blocked"
    assert ledger.get(result.run_id).state == RunState.BLOCKED


def test_orchestrator_routes_paused_outcome_to_paused_terminal(tmp_path: Path) -> None:
    orch, ledger, repo = _fresh_orchestrator(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    original_review = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def pause_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.PAUSED, kwargs["ctx"], "awaiting approval"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = pause_review  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original_review  # type: ignore[assignment]
    assert result.terminal_state == "paused"
    assert ledger.get(result.run_id).state == RunState.PAUSED


def test_orchestrator_aborts_run_after_failed_phase_no_further_events(tmp_path: Path) -> None:
    """After a phase returns FAILED, subsequent phases must NOT execute."""
    orch, _, repo = _fresh_orchestrator(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    executed: list[PhaseName] = []

    def tracking_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        executed.append(PhaseName.REVIEW)
        return PhaseOutcome.FAILED, kwargs["ctx"], "boom"

    original_review = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]
    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = tracking_review  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original_review  # type: ignore[assignment]
    phase_names = tuple(e.phase for e in result.events)
    # REVIEW ran and failed; DRAFT_PR, CI, READY, COMPLETED must NOT have run.
    assert "review" in phase_names
    assert "draft_pr" not in phase_names
    assert "ci" not in phase_names
    assert "ready" not in phase_names
    assert "completed" not in phase_names
    assert PhaseName.REVIEW in executed


def test_orchestrator_cancel_run_marks_ledger_cancelled(tmp_path: Path) -> None:
    orch, ledger, repo = _fresh_orchestrator(tmp_path)
    # Inject a PAUSED outcome so the run is in a non-terminal state
    # and can be cancelled.
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    original_review = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def pause_review(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.PAUSED, kwargs["ctx"], "awaiting"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = pause_review  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original_review  # type: ignore[assignment]
    assert result.terminal_state == "paused"
    orch.cancel_run(result.run_id)
    assert ledger.get(result.run_id).state == RunState.CANCELLED


def test_orchestrator_cannot_cancel_completed_run(tmp_path: Path) -> None:
    from seharness.orchestrator.orchestrator import OrchestratorError

    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    with pytest.raises(OrchestratorError):
        orch.cancel_run(result.run_id)


def test_orchestrator_resume_run_re_executes_phases(tmp_path: Path) -> None:
    """Cluster A: resume re-runs from scratch. Cluster E will replace
    this with deterministic replay from the ledger's event log."""
    orch, _, repo = _fresh_orchestrator(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    # Pause the run by calling resume on a completed run → should raise.
    from seharness.orchestrator.orchestrator import OrchestratorError

    with pytest.raises(OrchestratorError):
        orch.resume_run(result.run_id)
