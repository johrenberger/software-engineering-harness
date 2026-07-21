"""ControllerApplicationService — production impl of slice 11's Protocol.

Per SPEC §'21. OpenClaw packaging' — the wiring layer that
dispatches to:
- ``Orchestrator`` (Cluster A) for ``/feature`` — the canonical
  workflow engine that composes slice-3..slice-10 services
- ``CiMonitor`` (slice 10) for ``/pr``
- ``RunLedger`` for ``/status`` and ``/runs``

**Cluster A (story A3):** the controller now delegates ``/feature``
to the canonical ``Orchestrator`` instead of ``StubFeatureExecutor``.
The orchestrator is the single workflow engine — there is no other
path from a feature request to a draft PR. ``StubFeatureExecutor``
is retained for tests that don't want the full orchestrator
machinery.

**Returns dicts** (not Pydantic models) to satisfy the slice 11
Protocol's ``object`` return type. Slice 12 contract.

**No merge methods.** Protocol conformance enforces this.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..orchestrator import OrchestrationService
from ..telegram.service import FeatureRequest
from .run_ledger import RunLedger

_RUNS_LIMIT = 50


class FeatureExecutor(Protocol):
    """Protocol for the ``/feature`` executor.

    The canonical implementation is ``OrchestrationService`` (i.e.
    ``Orchestrator`` — Cluster A); the slice-12
    ``StubFeatureExecutor`` is retained for unit tests.

    Cluster WP1 / story WP1.5: this Protocol mirrors the
    ``OrchestrationService`` Protocol from the orchestrator module.
    The two are intentionally separate: ``FeatureExecutor`` exposes
    the dict-shaped legacy surface (``execute``/``resume``/``cancel``
    returning ``dict``), while ``OrchestrationService`` exposes the
    orchestrator's native ``start_run``/``resume_run``/``cancel_run``
    returning ``PipelineResult``. The controller dispatches between
    the two surfaces based on the executor's type. A future refactor
    could collapse these into one Protocol; we keep them separate
    today so the legacy ``StubFeatureExecutor`` path keeps working
    without forcing every test to migrate.
    """

    def execute(self, request: FeatureRequest) -> dict[str, Any]: ...

    def resume(self, run_id: str) -> dict[str, Any]: ...

    def cancel(self, run_id: str) -> dict[str, Any]: ...


def _coerce_result(result: Any) -> dict[str, Any]:
    """Normalize executor result to dict[str, Any]."""
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):
        dumped = result.model_dump()
        if isinstance(dumped, dict):
            return dumped
        return dict(dumped)
    return dict(result)


class StubFeatureExecutor:
    """Default ``FeatureExecutor`` impl used by tests + default wiring.

    Returns deterministic run_ids based on a monotonic counter so
    tests can assert on the call sequence.
    """

    def __init__(self) -> None:
        self._counter = 0
        self.last_request: FeatureRequest | None = None
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, request: FeatureRequest) -> dict[str, Any]:
        self._counter += 1
        run_id = f"run-{self._counter:03d}"
        self.calls.append(("execute", (request,)))
        self.last_request = request
        return {
            "ok": True,
            "run_id": run_id,
            "repository": request.repository_url,
        }

    def resume(self, run_id: str) -> dict[str, Any]:
        self.calls.append(("resume", (run_id,)))
        return {"ok": True, "run_id": run_id}

    def cancel(self, run_id: str) -> dict[str, Any]:
        self.calls.append(("cancel", (run_id,)))
        return {"ok": True, "run_id": run_id}


class ControllerApplicationService:
    """Production ApplicationService.

    Implements the ``ApplicationService`` Protocol via structural
    conformance. NO merge methods.

    **Cluster A:** ``task_executor`` should be a
    :class:`~seharness.orchestrator.OrchestrationService` (i.e.
    ``Orchestrator``); ``StubFeatureExecutor`` remains available for
    tests that don't need the full orchestrator.

    **Cluster WP1 / story WP1.5:** the dispatch logic in
    ``feature_request`` / ``resume`` / ``cancel`` no longer uses
    ``isinstance(self._task_executor, Orchestrator)``. Instead we
    probe for the ``OrchestrationService`` surface (``start_run`` /
    ``resume_run`` / ``cancel_run`` methods). This decouples the
    controller from the concrete ``Orchestrator`` class — any
    conformer (test doubles, future replacement engines) works as
    long as it satisfies :class:`OrchestrationService`.
    """

    def __init__(
        self,
        *,
        task_executor: FeatureExecutor,
        ci_monitor: object,
        run_ledger: RunLedger,
    ) -> None:
        self._task_executor = task_executor
        self._ci_monitor = ci_monitor
        self._run_ledger = run_ledger

    # --- dispatch helpers ----------------------------------------------

    def _orchestration_service(self) -> OrchestrationService | None:
        """Return the task executor as an OrchestrationService, or None.

        Cluster WP1 / story WP1.5: structural dispatch replaces the
        previous ``isinstance(self._task_executor, Orchestrator)``
        check. We probe the three OrchestrationService methods; if
        any are missing the executor is treated as a legacy
        ``StubFeatureExecutor`` (whose ``execute`` / ``resume`` /
        ``cancel`` methods return dicts directly).
        """
        executor = self._task_executor
        for method in ("start_run", "resume_run", "cancel_run"):
            if not hasattr(executor, method):
                return None
        # Cast through Any because Protocol conformance is structural;
        # mypy would otherwise need an explicit isinstance.
        return executor  # type: ignore[return-value]

    # --- /feature --------------------------------------------------------

    def feature_request(self, request: FeatureRequest) -> dict[str, Any]:
        # Cluster WP1 / story WP1.5: dispatch via the
        # OrchestrationService Protocol instead of
        # ``isinstance(self._task_executor, Orchestrator)``. This
        # decouples the controller from the concrete Orchestrator
        # class and lets any conformer (test doubles, future
        # replacement engines) work.
        orch = self._orchestration_service()
        if orch is not None:
            pipeline = orch.start_run(
                feature_description=request.description,
                repo_path=request.repository_url,
            )
            run_id: str = pipeline.run_id
            # Cluster WP1 / story WP1.3: propagate the pipeline
            # terminal state. ``ok=False`` is returned for
            # failed/blocked/cancelled so callers can branch without
            # re-querying the ledger. The legacy always-ok=True path
            # would silently mask delivery failures from the CLI /
            # Telegram handlers.
            terminal = pipeline.terminal_state
            ok = terminal == "completed"
            return {
                "ok": ok,
                "run_id": run_id,
                "repository": request.repository_url,
                "terminal_state": terminal,
            }
        else:
            raw = self._task_executor.execute(request)
            coerced = _coerce_result(raw)
            run_id = coerced.get("run_id") or "unknown"
        return {
            "ok": True,
            "run_id": run_id,
            "repository": request.repository_url,
        }

    # --- /resume ---------------------------------------------------------

    def resume(self, run_id: str) -> dict[str, Any]:
        # Cluster WP1 / story WP1.5: dispatch via the
        # OrchestrationService Protocol.
        orch = self._orchestration_service()
        if orch is not None:
            pipeline = orch.resume_run(run_id)
            # Cluster WP1 / story WP1.3: propagate the resumed
            # pipeline terminal state so callers learn whether the
            # resume itself succeeded or re-failed.
            terminal = pipeline.terminal_state
            ok = terminal == "completed"
            return {
                "ok": ok,
                "run_id": run_id,
                "terminal_state": terminal,
                "result": {"events": len(pipeline.events)},
            }
        raw = self._task_executor.resume(run_id)
        coerced = _coerce_result(raw)
        self._run_ledger.mark_resume(run_id)
        ok = bool(coerced.get("ok", True))
        return {"ok": ok, "run_id": run_id, "result": coerced}

    # --- /cancel ---------------------------------------------------------

    def cancel(self, run_id: str) -> dict[str, Any]:
        # Cluster WP1 / story WP1.5: dispatch via the
        # OrchestrationService Protocol.
        orch = self._orchestration_service()
        if orch is not None:
            orch.cancel_run(run_id)
            return {"ok": True, "run_id": run_id}
        raw = self._task_executor.cancel(run_id)
        coerced = _coerce_result(raw)
        self._run_ledger.mark_cancelled(run_id)
        ok = bool(coerced.get("ok", True))
        return {"ok": ok, "run_id": run_id, "result": coerced}

    # --- /status ---------------------------------------------------------

    def status(self, run_id: str) -> dict[str, Any]:
        rec = self._run_ledger.get(run_id)
        if rec is None:
            return {"ok": False, "state": "unknown", "run_id": run_id}
        return {
            "ok": True,
            "run_id": rec.run_id,
            "state": rec.state.value,
            "repository": rec.repository,
            "started_at": rec.started_at,
        }

    # --- /runs -----------------------------------------------------------

    def runs(self) -> tuple[str, ...]:
        """Return the run_ids of the most-recent ``_RUNS_LIMIT`` runs.

        Conforms to the slice-11 ``ApplicationService.runs`` Protocol
        (returns ``tuple[str, ...]``). The structured payload is
        available via ``status(run_id)``.
        """
        all_runs = self._run_ledger.runs
        ordered = tuple(reversed(all_runs))[:_RUNS_LIMIT]
        return tuple(r.run_id for r in ordered)

    # --- /pr -------------------------------------------------------------

    def pr_status(self, run_id: str) -> dict[str, Any]:
        rec = self._run_ledger.get(run_id)
        if rec is None:
            return {"ok": False, "error": "unknown run", "run_id": run_id}
        # We do NOT trigger the slice 10 ``CiMonitor.run`` (which polls
        # for up to ``max_attempts`` iterations). Instead, we ask the
        # monitor for its current view via ``view_factory`` and pass it
        # through ``ReadyEvaluator``. This keeps ``/pr`` instant and
        # never merges.
        view_factory = getattr(self._ci_monitor, "_view_factory", None)
        view = view_factory() if view_factory is not None else None
        if view is None:
            return {
                "ok": True,
                "run_id": run_id,
                "outcome": "unknown",
                "attempts_made": 0,
            }
        # Lazy import to avoid cycles.
        from ..ci.readiness import ReadyEvaluator  # noqa: PLC0415

        decision = ReadyEvaluator().evaluate(view)
        outcome_value = "ready" if decision.can_be_ready else "still_pending"
        return {
            "ok": True,
            "run_id": run_id,
            "outcome": outcome_value,
            "attempts_made": 1,
        }
