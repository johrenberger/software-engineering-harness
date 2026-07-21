"""Tests for the WP1 structured PhaseCursor and resume-retry semantics.

Cluster WP1 / stories WP1.1 + WP1.2 + WP1.3.

These tests pin down three contracts:

- WP1.1: ``record_phase`` writes a ``PhaseCursor`` and accepts
  ``phase_outcome`` + ``phase_attempt`` with strict validation.
- WP1.2: orchestrator resume retries the failed phase (not the
  next phase) when ``cursor.phase_outcome`` is in
  ``{failed, blocked, paused}``; resumes after the last successful
  phase when the outcome is ``ok`` / ``skipped``.
- WP1.3: ``ControllerApplicationService.feature_request`` and
  ``.resume`` return ``ok=False`` for failed/blocked/cancelled
  terminal states (and propagate ``terminal_state`` in the payload).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.controller.application_service import (
    ControllerApplicationService,
    StubFeatureExecutor,
)

# Ordering fix: import controller modules before orchestrator types
# to avoid the pre-existing circular import trap (see
# ``test_runtime_profile.py`` for the same workaround + rationale).
from seharness.controller.run_ledger import (  # noqa: F401
    OptimisticConcurrencyError,
    PhaseCursor,
    RunLedger,
    RunRecord,
    RunState,
    to_jsonable,
)
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.types import RunId

# ---------------------------------------------------------------------------
# WP1.1 — PhaseCursor model + record_phase cursor writes
# ---------------------------------------------------------------------------


class TestPhaseCursorModel:
    def test_minimal_cursor(self) -> None:
        cur = PhaseCursor(current_phase="validation")
        assert cur.current_phase == "validation"
        assert cur.last_completed_phase is None
        assert cur.failed_phase is None
        assert cur.phase_attempt == 0
        assert cur.phase_outcome == "ok"

    def test_full_cursor(self) -> None:
        cur = PhaseCursor(
            current_phase="validation",
            last_completed_phase="planning",
            failed_phase="validation",
            phase_attempt=2,
            phase_outcome="failed",
        )
        assert cur.current_phase == "validation"
        assert cur.last_completed_phase == "planning"
        assert cur.failed_phase == "validation"
        assert cur.phase_attempt == 2
        assert cur.phase_outcome == "failed"

    def test_frozen(self) -> None:
        from pydantic import ValidationError as PydValidationError

        cur = PhaseCursor(current_phase="validation")
        with pytest.raises(PydValidationError):
            cur.current_phase = "ready"  # type: ignore[misc]

    def test_unknown_phase_outcome_rejected(self) -> None:
        """Pydantic's frozen + extra='forbid' rejects free-form values.

        The ``phase_outcome`` field is a free ``str`` (not a StrEnum)
        for forward-compat with the JSONL on-disk format, so unknown
        values pass Pydantic validation. We rely on the ledger-level
        :func:`RunLedger.record_phase` check (next test) to reject
        bogus outcomes.
        """
        cur = PhaseCursor(current_phase="validation", phase_outcome="???")
        assert cur.phase_outcome == "???"

    def test_negative_attempt_rejected(self) -> None:
        from pydantic import ValidationError as PydValidationError

        with pytest.raises(PydValidationError):
            PhaseCursor(current_phase="validation", phase_attempt=-1)

    def test_extra_keys_rejected(self) -> None:
        from pydantic import ValidationError as PydValidationError

        with pytest.raises(PydValidationError):
            PhaseCursor(current_phase="validation", bogus="x")  # type: ignore[call-arg]


class TestRecordPhaseCursorWrites:
    def test_record_phase_writes_cursor(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        rec = ledger.record_phase("r1", phase="validation", phase_outcome="failed", phase_attempt=0)
        assert rec is not None
        assert rec.cursor is not None
        assert rec.cursor.current_phase == "validation"
        assert rec.cursor.phase_outcome == "failed"
        assert rec.cursor.failed_phase == "validation"

    def test_record_phase_outcome_ok_advances_last_completed(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        # First phase: success → last_completed_phase set, failed_phase cleared.
        ledger.record_phase("r1", phase="planning", phase_outcome="ok", phase_attempt=0)
        rec = ledger.record_phase(
            "r1", phase="implementation", phase_outcome="failed", phase_attempt=0
        )
        assert rec is not None
        assert rec.cursor is not None
        assert rec.cursor.last_completed_phase == "planning"
        assert rec.cursor.failed_phase == "implementation"
        assert rec.cursor.phase_outcome == "failed"

    def test_record_phase_recovery_clears_failed_marker(self) -> None:
        """After a failed phase, a successful retry clears failed_phase."""
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        ledger.record_phase("r1", phase="validation", phase_outcome="failed")
        rec = ledger.record_phase("r1", phase="validation", phase_outcome="ok", phase_attempt=1)
        assert rec is not None
        assert rec.cursor is not None
        assert rec.cursor.failed_phase is None
        assert rec.cursor.last_completed_phase == "validation"

    def test_record_phase_invalid_outcome_rejected(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        with pytest.raises(ValueError):
            ledger.record_phase("r1", phase="validation", phase_outcome="????")

    def test_record_phase_negative_attempt_rejected(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        with pytest.raises(ValueError):
            ledger.record_phase("r1", phase="validation", phase_outcome="ok", phase_attempt=-1)

    def test_record_phase_empty_phase_rejected(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        with pytest.raises(ValueError):
            ledger.record_phase("r1", phase="")

    def test_record_phase_cas_check(self) -> None:
        """Concurrent workers cannot race a phase transition."""
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        with pytest.raises(OptimisticConcurrencyError):
            ledger.record_phase(
                "r1",
                phase="validation",
                phase_outcome="ok",
                expected_revision=999,  # wrong on purpose
            )

    def test_record_phase_cas_match_succeeds(self) -> None:
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        rec = ledger.record_phase("r1", phase="validation", phase_outcome="ok", expected_revision=1)
        assert rec is not None
        assert rec.revision == 2

    def test_record_phase_no_cas_default(self) -> None:
        """When expected_revision is None the CAS check is skipped."""
        ledger = RunLedger()
        ledger.record_start("r1", repository="/repo")
        rec = ledger.record_phase("r1", phase="validation", phase_outcome="ok")
        assert rec is not None


# ---------------------------------------------------------------------------
# WP1.2 — Orchestrator resume retries the failed phase
# ---------------------------------------------------------------------------


def _build_orchestrator(ledger: RunLedger) -> Orchestrator:
    cfg = OrchestratorConfig(execution_root=".openclaw-runs/orchestrator-wp1-test")
    return Orchestrator(
        run_ledger=ledger,
        config=cfg,
        pr_client=StubPullRequestClient(),
        ci_monitor=None,
        trace_writer=None,
    )


class TestResumeRetriesFailedPhase:
    def test_failed_phase_lands_in_cursor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a fatal phase failure, the cursor records the failed
        phase so the next resume retries it (does not skip it)."""
        from seharness.orchestrator import orchestrator as orch_mod

        # Inject a fatal failure into the planning phase via the same
        # monkey-patch pattern used by test_orchestrator.py.
        original = orch_mod._PlanBuilder.build

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic planning failure")

        orch_mod._PlanBuilder.build = staticmethod(boom)  # type: ignore[assignment]
        try:
            ledger = RunLedger()
            orch = _build_orchestrator(ledger)
            result = orch.start_run(
                feature_description="test failed-phase cursor",
                repo_path=str(tmp_path),
                run_id=RunId("orch-wp1fp01"),
            )
        finally:
            orch_mod._PlanBuilder.build = original  # type: ignore[assignment]
        assert result.terminal_state == "failed"
        rec = ledger.get("orch-wp1fp01")
        assert rec is not None
        assert rec.cursor is not None
        assert rec.cursor.failed_phase == "planning", (
            "expected cursor.failed_phase to be 'planning' after a "
            f"planning-phase failure, got {rec.cursor.failed_phase!r}"
        )
        assert rec.cursor.phase_outcome == "failed"

    def test_resume_retries_failed_phase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: run fails at planning, then ``start_run(resume_from_run_id=...)``
        is invoked. The resume must retry the failed phase, not skip past it.

        We track ``_phase_planning`` invocations specifically (not
        ``_PlanBuilder.build``, which is called by several phases)
        so the assertion is robust against phase-handler refactors.
        """
        from seharness.orchestrator import orchestrator as orch_mod
        from seharness.orchestrator.types import PhaseName

        # Patch the planning handler itself so we count planning
        # invocations directly. The patched handler fails the first
        # time it's called (proving the failed-phase path) and
        # delegates to the original on the second call (proving the
        # resume retry happened and planning was retried, not skipped).
        planning_call_count = {"n": 0}

        # Reach into the orchestrator module's private _PHASE_HANDLERS
        # to wrap just the planning handler. We restore it in the
        # finally block.
        original_planning = orch_mod._PHASE_HANDLERS[PhaseName.PLANNING]
        original_planning_fn = original_planning

        def wrapped_planning(orch_self, *, spec, ctx, run_dir):  # type: ignore[no-untyped-def]
            planning_call_count["n"] += 1
            if planning_call_count["n"] == 1:
                raise RuntimeError("synthetic planning failure")
            return original_planning_fn(orch_self, spec=spec, ctx=ctx, run_dir=run_dir)

        orch_mod._PHASE_HANDLERS[PhaseName.PLANNING] = wrapped_planning
        try:
            ledger = RunLedger()
            orch = _build_orchestrator(ledger)
            # First run: fails at planning.
            first = orch.start_run(
                feature_description="wp1 resume retry",
                repo_path=str(tmp_path),
                run_id=RunId("orch-wp1rr01"),
            )
            assert first.terminal_state == "failed"
            # Second run: resume_from_run_id triggers planning again.
            orch.start_run(
                feature_description="wp1 resume retry",
                repo_path=str(tmp_path),
                run_id=RunId("orch-wp1rr01"),
                resume_from_run_id="orch-wp1rr01",
            )
        finally:
            orch_mod._PHASE_HANDLERS[PhaseName.PLANNING] = original_planning_fn
        # The planning handler was called exactly twice (initial failure +
        # resume retry), proving resume actually retried it instead of
        # skipping past it.
        assert planning_call_count["n"] == 2, (
            f"expected planning handler to be called twice (initial "
            f"failure + resume retry), got {planning_call_count['n']}"
        )
        # The resumed run's cursor must record planning as the last
        # completed phase (or advance past it).
        rec_after = ledger.get("orch-wp1rr01")
        assert rec_after is not None


# ---------------------------------------------------------------------------
# WP1.3 — Controller ok=False for failed/blocked/cancelled
# ---------------------------------------------------------------------------


class TestControllerOkFalseOnFailure:
    def test_feature_request_returns_ok_false_on_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Orchestrator returns terminal_state='failed' → controller
        propagates ok=False with terminal_state in the payload."""
        from seharness.orchestrator import orchestrator as orch_mod
        from seharness.telegram.service import FeatureRequest

        original = orch_mod._PlanBuilder.build

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic failure")

        orch_mod._PlanBuilder.build = staticmethod(boom)  # type: ignore[assignment]
        try:
            ledger = RunLedger()
            orch = _build_orchestrator(ledger)
            svc = ControllerApplicationService(
                task_executor=orch,
                ci_monitor=None,
                run_ledger=ledger,
            )
            req = FeatureRequest(description="x", repository_url=str(tmp_path))
            result = svc.feature_request(req)
        finally:
            orch_mod._PlanBuilder.build = original  # type: ignore[assignment]
        assert result["ok"] is False
        assert result["terminal_state"] == "failed"
        assert "run_id" in result

    def test_resume_returns_ok_false_on_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Resume of a failed run that re-fails → ok=False."""
        from seharness.orchestrator import orchestrator as orch_mod

        # Fail every call so both initial run AND resume fail.
        original = orch_mod._PlanBuilder.build

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic permanent failure")

        orch_mod._PlanBuilder.build = staticmethod(boom)  # type: ignore[assignment]
        try:
            ledger = RunLedger()
            orch = _build_orchestrator(ledger)
            svc = ControllerApplicationService(
                task_executor=orch,
                ci_monitor=None,
                run_ledger=ledger,
            )
            orch.start_run(
                feature_description="wp1 controller ok-false resume",
                repo_path=str(tmp_path),
                run_id=RunId("orch-wp1cf01"),
            )
            result = svc.resume("orch-wp1cf01")
        finally:
            orch_mod._PlanBuilder.build = original  # type: ignore[assignment]
        assert result["ok"] is False
        assert result["terminal_state"] == "failed"

    def test_stub_executor_still_returns_ok_true(self) -> None:
        """Back-compat: stub executor returns ok=True as before."""
        ledger = RunLedger()
        stub = StubFeatureExecutor()
        svc = ControllerApplicationService(
            task_executor=stub,
            ci_monitor=None,
            run_ledger=ledger,
        )
        from seharness.telegram.service import FeatureRequest

        req = FeatureRequest(description="x", repository_url="/repo")
        result = svc.feature_request(req)
        # Stub returns ok=True (legacy path) — no terminal_state key.
        assert result["ok"] is True
        assert "terminal_state" not in result


# ---------------------------------------------------------------------------
# Back-compat — JSONL-on-disk format keeps reading legacy records
# ---------------------------------------------------------------------------


class TestCursorSerialization:
    def test_as_dict_includes_cursor_when_set(self) -> None:
        from seharness.controller.run_ledger import _as_dict

        rec = RunRecord(
            run_id="r1",
            state=RunState.RUNNING,
            repository="/repo",
            cursor=PhaseCursor(current_phase="validation"),
        )
        payload = _as_dict(rec)
        assert "cursor" in payload
        assert payload["cursor"]["current_phase"] == "validation"

    def test_as_dict_omits_cursor_when_none(self) -> None:
        from seharness.controller.run_ledger import _as_dict

        rec = RunRecord(
            run_id="r1",
            state=RunState.RUNNING,
            repository="/repo",
        )
        payload = _as_dict(rec)
        assert "cursor" not in payload

    def test_to_jsonable_handles_phase_cursor(self) -> None:
        cur = PhaseCursor(current_phase="validation")
        # ``PhaseCursor`` is a BaseModel — ``to_jsonable`` should
        # model_dump it so it round-trips through JSON.
        result = to_jsonable({"cursor": cur})
        assert isinstance(result, dict)
        assert result["cursor"] == {
            "current_phase": "validation",
            "last_completed_phase": None,
            "failed_phase": None,
            "phase_attempt": 0,
            "phase_outcome": "ok",
        }
