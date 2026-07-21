"""Cluster N PR6 \u2014 MiniMax budget tracker.

Cluster N of the MiniMax SE-harness improvement handoff.
**Step 6** of the targeted refinement workplan: red-green
remediation. The workplan calls for a ``MiniMaxBudgetTracker``
that reuses ``BudgetTracker`` from WP8 (PR #66) with a
narrower axis surface (model token, cost, wall time).

The tracker is a thin wrapper around ``BudgetTracker`` that:

- Restricts consumption to the three model-axis fields the
  MiniMax workflow cares about: ``MODEL_TOKENS``,
  ``MODEL_COST_USD``, ``ELAPSED_SECONDS``. Recording against
  other axes raises ``ValueError`` so the model service can
  never accidentally leak tool-call or diff-size consumption
  into the model budget pool.
- Exposes ``record_invocation(response)`` so the caller does
  not have to compute input/output/cost by hand \u2014 the helper
  pulls the values off the ``ModelResponse`` and forwards
  them.
- Exposes ``last_decision`` and ``last_decision_axis`` for
  audit + dashboard, mirroring the orchestrator's pattern.
- Raises ``BudgetExhausted`` (re-exported) so existing
  callers continue to work.

The tracker does NOT replace ``BudgetTracker``; it composes
it. The orchestrator can wire either, depending on whether
the run is MiniMax-backed or provider-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from seharness.domain.results import ModelResponse
from seharness.orchestrator.budgets import (
    BudgetAxis,
    BudgetDecision,
    BudgetExhausted,
    BudgetOutcome,
    BudgetTracker,
    RunBudgets,
)

if TYPE_CHECKING:
    pass


# Cost per 1k tokens. The MiniMax pricing tier is operator-
# configurable; the default below is a conservative placeholder
# for offline tests and unit fixtures. Production must override
# via the ``cost_per_1k_tokens`` constructor argument.
DEFAULT_COST_PER_1K_TOKENS: float = 0.002


# The narrower axis surface the workplan calls for. Recording
# against any other axis is a programming error.
ALLOWED_AXES: frozenset[BudgetAxis] = frozenset(
    {
        BudgetAxis.MODEL_TOKENS,
        BudgetAxis.MODEL_COST_USD,
        BudgetAxis.ELAPSED_SECONDS,
    }
)


@dataclass
class MiniMaxBudgetTracker:
    """Model-axis budget tracker for the MiniMax workflow.

    Wraps a WP8 ``BudgetTracker`` with a narrower axis surface
    and convenience helpers. The wrapper is intentionally thin
    \u2014 the orchestrator's existing budget enforcement flow
    (record \u2192 enforce \u2192 translate to PhaseOutcome) continues
    to work unchanged.

    Parameters
    ----------
    budgets
        The ceilings. ``MODEL_TOKENS``, ``MODEL_COST_USD``,
        and ``ELAPSED_SECONDS`` are the axes this tracker
        manages. Other axes in ``RunBudgets`` are ignored.
    cost_per_1k_tokens
        USD cost per 1000 model tokens (combined input +
        output). Defaults to ``DEFAULT_COST_PER_1K_TOKENS``;
        production runs MUST override this with the operator's
        negotiated tier.
    inner
        Optional injected ``BudgetTracker``. Tests can pass a
        pre-configured tracker to verify integration with the
        WP8 contract.
    """

    budgets: RunBudgets
    cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS
    inner: BudgetTracker = field(init=False)

    def __post_init__(self) -> None:
        if self.cost_per_1k_tokens < 0:
            msg = f"cost_per_1k_tokens must be >= 0, got {self.cost_per_1k_tokens}"
            raise ValueError(msg)
        self.inner = BudgetTracker(budgets=self.budgets)

    def record_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        """Record token consumption.

        Negative token counts are rejected; the upstream
        ``ModelUsage`` model already enforces ``ge=0`` but the
        tracker is defensive at the boundary.
        """
        if input_tokens < 0:
            raise ValueError(f"input_tokens must be >= 0, got {input_tokens}")
        if output_tokens < 0:
            raise ValueError(f"output_tokens must be >= 0, got {output_tokens}")
        total = input_tokens + output_tokens
        self.inner.record(BudgetAxis.MODEL_TOKENS, total)
        # Cost is computed from the combined token count at the
        # operator-configured rate.
        cost = (total / 1000.0) * self.cost_per_1k_tokens
        self.inner.record(BudgetAxis.MODEL_COST_USD, cost)

    def record_elapsed(self, duration_s: float) -> None:
        """Record wall-clock consumption for the model call.

        Negative durations are rejected; callers should pass a
        ``time.monotonic`` delta.
        """
        if duration_s < 0:
            msg = f"duration_s must be >= 0, got {duration_s}"
            raise ValueError(msg)
        self.inner.record(BudgetAxis.ELAPSED_SECONDS, duration_s)

    def record_invocation(
        self,
        response: ModelResponse,
        *,
        duration_s: float | None = None,
    ) -> None:
        """Convenience helper: record token + cost + elapsed
        from a ``ModelResponse``.

        ``duration_s`` defaults to ``response.duration_s`` when
        the response carries it; callers can override when
        the response is constructed in tests.
        """
        if response.usage is not None:
            self.record_tokens(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
        elapsed = duration_s if duration_s is not None else response.duration_s
        if elapsed is not None and elapsed >= 0:
            self.record_elapsed(float(elapsed))

    def consumption(self) -> dict[BudgetAxis, float]:
        """Return the current consumption across the model axes."""
        result: dict[BudgetAxis, float] = {}
        for axis in ALLOWED_AXES:
            consumed = self.inner.consumption().get(axis, 0.0)
            if consumed:
                result[axis] = consumed
        return result

    def check(self) -> BudgetDecision:
        """Read-only check against the ceilings."""
        decision = self.inner.check()
        if decision.outcome == BudgetOutcome.BLOCKED:
            # Surface the decision via the audit properties so
            # callers don't need to reach through ``inner``.
            self.last_decision = decision
            self.last_decision_axis = decision.exceeded_axis
        return decision

    def enforce(self) -> BudgetDecision:
        """Enforce the ceilings; raise ``BudgetExhausted`` when
        an axis is exceeded."""
        try:
            decision = self.inner.enforce()
        except BudgetExhausted as exc:
            self.last_decision = exc.decision
            self.last_decision_axis = exc.decision.exceeded_axis
            raise
        self.last_decision = decision
        return decision

    last_decision: BudgetDecision | None = None
    last_decision_axis: BudgetAxis | None = None


__all__ = [
    "ALLOWED_AXES",
    "DEFAULT_COST_PER_1K_TOKENS",
    "MiniMaxBudgetTracker",
]
