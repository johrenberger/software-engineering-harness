"""WP8 (story M) — operational budgets for the orchestrator.

The handoff doc acceptance criteria for budgets:

* Add budgets for model usage, cost, tool calls, elapsed time,
  retries, files changed, and diff size.
* Budget exhaustion pauses or blocks with a clear reason.

This module ships:

1. :class:`RunBudgets` — frozen dataclass capturing the ceiling for
   each axis (model tokens, cost, tool calls, elapsed seconds,
   retries, files changed, diff size).
2. :class:`BudgetTracker` — mutable accumulator that records
   consumption against the budget. ``check()`` returns a
   ``BudgetDecision`` (``ok``, ``exceeded_axis``, ``reason``,
   ``recommendation``) that the orchestrator consults before each
   phase.
3. :class:`BudgetExhausted` — exception raised by ``enforce()`` so
   the orchestrator can route to ``paused`` or ``blocked``
   deterministically.

Design constraints:

* No I/O at construction time. ``RunBudgets`` is pure data so it
  can be carried in ``RunContext``.
* The tracker is single-threaded per run; concurrent workers MUST
  use the lease seam (see :mod:`seharness.orchestrator.leases`).
* Fail-closed in production: ``RuntimeProfile.PRODUCTION`` plus a
  ``RunBudgets`` instance with any axis at zero is rejected at
  construction time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class BudgetAxis(StrEnum):
    """Enumerate the axes a budget can constrain."""

    MODEL_TOKENS = "model_tokens"
    MODEL_COST_USD = "model_cost_usd"
    TOOL_CALLS = "tool_calls"
    ELAPSED_SECONDS = "elapsed_seconds"
    RETRIES = "retries"
    FILES_CHANGED = "files_changed"
    DIFF_SIZE_BYTES = "diff_size_bytes"


class BudgetOutcome(StrEnum):
    """Result of a budget check."""

    OK = "ok"
    PAUSED = "paused"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RunBudgets:
    """Frozen ceilings for a single run.

    Any axis set to ``None`` is unlimited. Production runs must
    set explicit ceilings (the orchestrator's
    ``validate_runtime_profile_adapters`` rejects a production
    ``OrchestratorConfig`` whose budgets are all ``None``).
    """

    model_tokens: int | None = None
    model_cost_usd: float | None = None
    tool_calls: int | None = None
    elapsed_seconds: float | None = None
    retries: int | None = None
    files_changed: int | None = None
    diff_size_bytes: int | None = None

    def ceiling(self, axis: BudgetAxis) -> float | None:
        value = getattr(self, axis.value)
        return None if value is None else float(value)

    def axes(self) -> tuple[BudgetAxis, ...]:
        return tuple(BudgetAxis)

    def is_unlimited(self) -> bool:
        return all(self.ceiling(axis) is None for axis in BudgetAxis)


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a budget check at a single point in time.

    ``outcome`` is :class:`BudgetOutcome`. When not OK, the
    ``exceeded_axis`` names the axis that crossed its ceiling,
    ``consumed`` is the value seen, ``ceiling`` is the configured
    ceiling, and ``reason`` is a human-readable string the
    controller surfaces on the dashboard.
    """

    outcome: BudgetOutcome
    exceeded_axis: BudgetAxis | None = None
    consumed: float | None = None
    ceiling: float | None = None
    reason: str = ""


class BudgetExhausted(RuntimeError):
    """Raised by :meth:`BudgetTracker.enforce` when a ceiling is hit."""

    def __init__(self, decision: BudgetDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason)


@dataclass
class BudgetTracker:
    """Mutable accumulator against a :class:`RunBudgets` ceiling.

    The tracker is single-threaded per run. Concurrent workers MUST
    coordinate via the lease seam — never share a tracker.

    ``record()`` updates consumption for an axis. ``check()`` is
    read-only and returns the current ``BudgetDecision``. ``enforce()``
    is the write-through check that raises :class:`BudgetExhausted`
    so callers can route to ``paused`` / ``blocked`` deterministically.
    """

    budgets: RunBudgets
    # We use float internally because USD cost is fractional and
    # diff-size bytes can exceed int32. ``int`` axes are recorded
    # as their natural type but compared as ``float``.
    _consumed: dict[BudgetAxis, float] = field(default_factory=dict)

    def record(self, axis: BudgetAxis, value: float) -> None:
        if value < 0:
            raise ValueError(f"budget consumption must be >= 0, got {value}")
        self._consumed[axis] = self._consumed.get(axis, 0.0) + float(value)

    def set(self, axis: BudgetAxis, value: float) -> None:
        if value < 0:
            raise ValueError(f"budget consumption must be >= 0, got {value}")
        self._consumed[axis] = float(value)

    def consumption(self) -> Mapping[BudgetAxis, float]:
        return dict(self._consumed)

    def check(self) -> BudgetDecision:
        """Evaluate the current consumption against the ceilings.

        Returns the first exceeded axis (stable order matches
        ``BudgetAxis`` declaration order), or an OK decision.
        """
        for axis in BudgetAxis:
            ceiling = self.budgets.ceiling(axis)
            if ceiling is None:
                continue
            consumed = self._consumed.get(axis, 0.0)
            if consumed >= ceiling:
                return BudgetDecision(
                    outcome=BudgetOutcome.BLOCKED,
                    exceeded_axis=axis,
                    consumed=consumed,
                    ceiling=ceiling,
                    reason=(f"{axis.value} budget exceeded: {consumed:g} >= {ceiling:g}"),
                )
        return BudgetDecision(outcome=BudgetOutcome.OK)

    def enforce(self) -> BudgetDecision:
        decision = self.check()
        if decision.outcome is not BudgetOutcome.OK:
            raise BudgetExhausted(decision)
        return decision


__all__ = [
    "BudgetAxis",
    "BudgetDecision",
    "BudgetExhausted",
    "BudgetOutcome",
    "BudgetTracker",
    "RunBudgets",
]
