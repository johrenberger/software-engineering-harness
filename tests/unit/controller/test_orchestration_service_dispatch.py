"""Tests for WP1 sub-task F — OrchestrationService Protocol + structural dispatch.

Cluster WP1 / story WP1.5.

The controller used to dispatch to ``Orchestrator`` via
``isinstance(self._task_executor, Orchestrator)``. This was a
hard-coupling that prevented test doubles (and future replacement
engines) from satisfying the controller without subclassing
``Orchestrator``. After this change, the controller probes for the
``OrchestrationService`` surface structurally (via ``hasattr``), and
any object with ``start_run`` + ``resume_run`` + ``cancel_run``
methods is dispatched to as an OrchestrationService.

Tests:
- Protocol shape matches Orchestrator's public surface.
- A minimal conformer (no inheritance) is dispatched to.
- A class missing one of the three methods is treated as a legacy
  FeatureExecutor (StubFeatureExecutor path).
- Back-compat: the existing ``Orchestrator`` instance is still
  dispatched to (i.e. behaviour preserved).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from seharness.controller.application_service import (
    ControllerApplicationService,
    StubFeatureExecutor,
)

# Ordering fix: import controller modules first to break the
# pre-existing orchestrator↔controller circular-import trap.
from seharness.controller.run_ledger import (  # noqa: F401
    PhaseCursor,
    RunLedger,
    RunState,
)
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import (
    OrchestrationService,
    Orchestrator,
    OrchestratorConfig,
)
from seharness.orchestrator.orchestrator import PipelineResult
from seharness.orchestrator.types import RunId
from seharness.telegram.service import FeatureRequest

# ---------------------------------------------------------------------------
# Protocol shape
# ---------------------------------------------------------------------------


class TestOrchestrationServiceProtocol:
    def test_orchestrator_satisfies_protocol(self) -> None:
        """``Orchestrator`` is a structural conformer of
        ``OrchestrationService`` — no inheritance required."""

        # mypy's static check isn't available at runtime, so we
        # probe the three methods directly. If any are missing the
        # test fails. This mirrors the dispatch probe in
        # ``ControllerApplicationService._orchestration_service``.
        cfg = OrchestratorConfig()
        ledger = RunLedger()
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            pr_client=StubPullRequestClient(),
            ci_monitor=None,
            trace_writer=None,
        )
        assert hasattr(orch, "start_run")
        assert hasattr(orch, "resume_run")
        assert hasattr(orch, "cancel_run")
        assert callable(orch.start_run)
        assert callable(orch.resume_run)
        assert callable(orch.cancel_run)

    def test_minimal_conformer_satisfies_protocol(self) -> None:
        """A minimal class with the three methods satisfies the
        Protocol structurally. No inheritance required."""

        class _MinimalConformer:
            def start_run(self, **kwargs: Any) -> PipelineResult:
                return PipelineResult(run_id="x", terminal_state="completed")

            def resume_run(self, run_id: str) -> PipelineResult:
                return PipelineResult(run_id=run_id, terminal_state="completed")

            def cancel_run(self, run_id: str) -> None:
                return None

        conformer: OrchestrationService = _MinimalConformer()
        # Structural conformance: probing the three methods is
        # equivalent to the runtime dispatch the controller does.
        assert hasattr(conformer, "start_run")
        assert hasattr(conformer, "resume_run")
        assert hasattr(conformer, "cancel_run")


# ---------------------------------------------------------------------------
# Controller dispatch via OrchestrationService surface
# ---------------------------------------------------------------------------


@dataclass
class _StubOrchestrationService:
    """A minimal OrchestrationService implementation for testing
    the controller's structural dispatch.

    Records every method call so tests can assert on the dispatch
    path. Returns deterministic PipelineResult values."""

    calls: list[tuple[str, tuple[Any, ...]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def start_run(
        self,
        *,
        feature_description: str,
        repo_path: str,
        run_id: RunId | None = None,
        idempotency_key: str = "",
        resume_from_run_id: str | None = None,
    ) -> PipelineResult:
        self.calls.append(("start_run", (feature_description, repo_path)))
        return PipelineResult(
            run_id=run_id or RunId("stub-orch-001"),
            terminal_state="completed",
        )

    def resume_run(self, run_id: str) -> PipelineResult:
        self.calls.append(("resume_run", (run_id,)))
        return PipelineResult(run_id=run_id, terminal_state="completed")

    def cancel_run(self, run_id: str) -> None:
        self.calls.append(("cancel_run", (run_id,)))


def _build_controller(
    executor: object, *, ledger: RunLedger | None = None
) -> ControllerApplicationService:
    if ledger is None:
        ledger = RunLedger()
    return ControllerApplicationService(
        task_executor=executor,  # type: ignore[arg-type]
        ci_monitor=None,
        run_ledger=ledger,
    )


class TestControllerDispatchToConformer:
    def test_conformer_dispatched_via_protocol(self) -> None:
        """A non-Orchestrator conformer is dispatched to via the
        OrchestrationService Protocol (no isinstance required)."""
        conformer = _StubOrchestrationService()
        svc = _build_controller(conformer)
        req = FeatureRequest(description="x", repository_url="/repo")
        result = svc.feature_request(req)
        # Dispatch went to the conformer's start_run, not the legacy
        # StubFeatureExecutor.execute path.
        assert any(call[0] == "start_run" for call in conformer.calls)
        assert result["ok"] is True
        assert result["terminal_state"] == "completed"

    def test_orchestrator_dispatched_via_protocol(self) -> None:
        """Existing Orchestrator instances still dispatch correctly
        via the new Protocol-based path (back-compat with PR1)."""
        cfg = OrchestratorConfig(execution_root=".openclaw-runs/orch-pr2-dispatch-test")
        ledger = RunLedger()
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            pr_client=StubPullRequestClient(),
            ci_monitor=None,
            trace_writer=None,
        )
        svc = _build_controller(orch, ledger=ledger)
        req = FeatureRequest(description="x", repository_url="/tmp")
        result = svc.feature_request(req)
        # WP1.3 contract: ok=False for non-completed, ok=True for completed.
        # The default Orchestrator terminates in "completed" for a
        # valid feature request, so ok should be True.
        assert "ok" in result
        assert "terminal_state" in result


class TestControllerDispatchToLegacyExecutor:
    def test_legacy_stub_executor_dispatched_to_execute(self) -> None:
        """A legacy ``StubFeatureExecutor`` (which has
        ``execute`` / ``resume`` / ``cancel`` but NOT the
        OrchestrationService methods) is dispatched via the
        legacy code path — backwards compatibility preserved."""
        stub = StubFeatureExecutor()
        svc = _build_controller(stub)
        req = FeatureRequest(description="x", repository_url="/repo")
        result = svc.feature_request(req)
        # Legacy path returns ok=True without terminal_state.
        assert result["ok"] is True
        assert "terminal_state" not in result
        # Stub records the call on .calls.
        assert any(call[0] == "execute" for call in stub.calls)

    def test_partial_conformer_falls_back_to_legacy(self) -> None:
        """A class that has SOME but not all OrchestrationService
        methods is treated as a legacy executor (no partial dispatch).
        The probe in ``_orchestration_service`` is all-or-nothing."""

        class _PartialConformer:
            def execute(self, request: FeatureRequest) -> dict[str, Any]:
                return {"ok": True, "run_id": "partial-001"}

            def start_run(self, **kwargs: Any) -> PipelineResult:  # type: ignore[override]
                return PipelineResult(run_id="partial-002", terminal_state="completed")

            # Missing resume_run and cancel_run

        partial: object = _PartialConformer()
        svc = _build_controller(partial)  # type: ignore[arg-type]
        req = FeatureRequest(description="x", repository_url="/repo")
        result = svc.feature_request(req)
        # Partial conformer has only start_run; probe returns None
        # because resume_run + cancel_run are missing → legacy path.
        assert result["ok"] is True
        assert "terminal_state" not in result
        assert result["run_id"] == "partial-001"


class TestConformerCancelAndResume:
    def test_conformer_resume_dispatches_to_resume_run(self) -> None:
        conformer = _StubOrchestrationService()
        svc = _build_controller(conformer)
        result = svc.resume("run-001")
        assert any(call[0] == "resume_run" for call in conformer.calls)
        assert result["ok"] is True
        assert result["terminal_state"] == "completed"

    def test_conformer_cancel_dispatches_to_cancel_run(self) -> None:
        conformer = _StubOrchestrationService()
        svc = _build_controller(conformer)
        result = svc.cancel("run-001")
        assert any(call[0] == "cancel_run" for call in conformer.calls)
        assert result["ok"] is True

    def test_conformer_failed_terminal_propagates(self) -> None:
        """WP1.3 contract: conformer terminal_state='failed' →
        controller propagates ok=False."""

        @dataclass
        class _FailingConformer:
            calls: list[tuple[str, tuple[Any, ...]]] = None  # type: ignore[assignment]

            def __post_init__(self) -> None:
                if self.calls is None:
                    self.calls = []

            def start_run(
                self,
                *,
                feature_description: str,
                repo_path: str,
                run_id: RunId | None = None,
                idempotency_key: str = "",
                resume_from_run_id: str | None = None,
            ) -> PipelineResult:
                self.calls.append(("start_run", (feature_description,)))
                return PipelineResult(run_id=run_id or RunId("x"), terminal_state="failed")

            def resume_run(self, run_id: str) -> PipelineResult:
                self.calls.append(("resume_run", (run_id,)))
                return PipelineResult(run_id=run_id, terminal_state="failed")

            def cancel_run(self, run_id: str) -> None:
                self.calls.append(("cancel_run", (run_id,)))

        conformer = _FailingConformer()
        svc = _build_controller(conformer)
        req = FeatureRequest(description="x", repository_url="/repo")
        result = svc.feature_request(req)
        assert result["ok"] is False
        assert result["terminal_state"] == "failed"
