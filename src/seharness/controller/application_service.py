"""ControllerApplicationService — production impl of slice 11's Protocol.

Per SPEC §'21. OpenClaw packaging' — the wiring layer that
dispatches to:
- ``FeatureExecutor`` for ``/feature`` (slice 12 adapter wrapping
  slice 7's ``TaskExecutionService`` or a stub)
- ``CiMonitor`` (slice 10) for ``/pr``
- ``RunLedger`` for ``/status`` and ``/runs``

The ``FeatureExecutor`` is its own Protocol (not slice 7's
``TaskExecutionService`` directly) because the CLI entry point is
``Plan → task_id`` while ``/feature`` is a high-level
``FeatureRequest``. The CLI wiring layer translates Plan → feature
request; for slice 12, we ship a ``StubFeatureExecutor`` that
returns a deterministic run_id.

**Returns dicts** (not Pydantic models) to satisfy the slice 11
Protocol's ``object`` return type. Slice 12 contract.

**No merge methods.** Protocol conformance enforces this.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..telegram.service import FeatureRequest
from .run_ledger import RunLedger

_RUNS_LIMIT = 50


class FeatureExecutor(Protocol):
    """Protocol for the ``/feature`` executor.

    The slice 12 wiring layer implements this with a stub that
    returns deterministic run_ids; slice 12+ may swap in a real
    controller that calls the same code path as the CLI.
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
        result = self._task_executor.execute(request)
        coerced = _coerce_result(result)
        run_id = coerced.get("run_id") or "unknown"
        self._run_ledger.record_start(run_id, repository=request.repository_url)
        return {
            "ok": True,
            "run_id": run_id,
            "repository": request.repository_url,
        }

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

    # --- /resume ---------------------------------------------------------

    def resume(self, run_id: str) -> dict[str, Any]:
        result = self._task_executor.resume(run_id)
        coerced = _coerce_result(result)
        self._run_ledger.mark_resume(run_id)
        ok = bool(coerced.get("ok", True))
        return {"ok": ok, "run_id": run_id, "result": coerced}

    # --- /cancel ---------------------------------------------------------

    def cancel(self, run_id: str) -> dict[str, Any]:
        result = self._task_executor.cancel(run_id)
        coerced = _coerce_result(result)
        self._run_ledger.mark_cancelled(run_id)
        ok = bool(coerced.get("ok", True))
        return {"ok": ok, "run_id": run_id, "result": coerced}

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
