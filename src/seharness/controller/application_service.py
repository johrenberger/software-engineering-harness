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

from typing import TYPE_CHECKING, Any, Protocol

from ..orchestrator import Orchestrator
from ..telegram.service import FeatureRequest
from .run_ledger import RunLedger

if TYPE_CHECKING:
    pass

_RUNS_LIMIT = 50


class FeatureExecutor(Protocol):
    """Protocol for the ``/feature`` executor.

    The canonical implementation is ``Orchestrator`` (Cluster A); the
    slice-12 ``StubFeatureExecutor`` is retained for unit tests.
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

    **Cluster A:** ``task_executor`` should be an ``Orchestrator``;
    ``StubFeatureExecutor`` remains available for tests that don't
    need the full orchestrator.
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

    # --- /feature --------------------------------------------------------

    def feature_request(self, request: FeatureRequest) -> dict[str, Any]:
        # Cluster A: delegate to the canonical orchestrator when wired.
        # ``Orchestrator`` satisfies the FeatureExecutor Protocol via
        # duck typing (start_run/resume_run/cancel_run methods).
        if isinstance(self._task_executor, Orchestrator):
            pipeline = self._task_executor.start_run(
                feature_description=request.description,
                repo_path=request.repository_url,
            )
            run_id: str = pipeline.run_id
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
        if isinstance(self._task_executor, Orchestrator):
            pipeline = self._task_executor.resume_run(run_id)
            return {"ok": True, "run_id": run_id, "result": {"events": len(pipeline.events)}}
        raw = self._task_executor.resume(run_id)
        coerced = _coerce_result(raw)
        self._run_ledger.mark_resume(run_id)
        ok = bool(coerced.get("ok", True))
        return {"ok": ok, "run_id": run_id, "result": coerced}

    # --- /cancel ---------------------------------------------------------

    def cancel(self, run_id: str) -> dict[str, Any]:
        if isinstance(self._task_executor, Orchestrator):
            self._task_executor.cancel_run(run_id)
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
