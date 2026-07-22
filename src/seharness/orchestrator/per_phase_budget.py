"""Cluster P1 \u2014 per-phase model-axis budget tracking.

Cluster P of the deferred follow-up work. The deferred item
(from WP10 closeout) was: **per-axis model token/cost tracking
at the services layer.** The workplan asks for the model
axis (model tokens, model cost) to be tracked per phase and
exposed for the dashboard + audit trail.

This module introduces
:class:`PerPhaseBudgetRecorder`, a thin wrapper around the WP8
:class:`BudgetTracker` that:

- Accepts ``record(phase_id, axis, value)`` instead of just
  ``record(axis, value)``.
- Forwards writes to the underlying tracker (so ``check()`` /
  ``enforce()`` behaviour is unchanged \u2014 cluster WP8 guarantees
  are preserved).
- Exposes ``consumption_by_phase() -> dict[phase_id,
  dict[axis, float]]`` so the dashboard / ``<run_dir>/budget/
  by-phase.json`` artifact can show per-phase breakdowns.
- Knows about the **model axes** (MODEL_TOKENS, MODEL_COST_USD,
  ELAPSED_SECONDS); recording other axes through this recorder
  raises ``ValueError`` so we don't leak non-model tracking
  into the per-phase breakdown stream.
- Persists the per-phase breakdown to
  ``<run_dir>/budget/by-phase.json`` via
  :func:`persist_by_phase`.

The recorder is intentionally orthogonal to the broader
budget tracker: it composes rather than replaces. Cluster N's
``MiniMaxBudgetTracker`` records flat; the recorder breaks
the consumption down by phase.

Backward compatibility
----------------------
- :class:`BudgetTracker` API unchanged; the recorder wraps it.
- ``consumption_by_phase()`` returns empty mapping until at
  least one phase is recorded.
- Phase IDs are strings (no enum); test fixtures use
  ``"spec"``, ``"plan"``, ``"implement"``,\u00a0\u2026
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from seharness.orchestrator.budgets import (
    BudgetAxis,
    BudgetDecision,
    BudgetTracker,
    RunBudgets,
)

# Axes the deferred follow-up explicitly asked to track per-
# phase. Other axes are tracked as before via the underlying
# ``BudgetTracker`` but the per-phase breakdown focuses on the
# model axes.
PER_PHASE_MODEL_AXES: Final[frozenset[BudgetAxis]] = frozenset(
    {
        BudgetAxis.MODEL_TOKENS,
        BudgetAxis.MODEL_COST_USD,
        BudgetAxis.ELAPSED_SECONDS,
    }
)


@dataclass
class PerPhaseBudgetRecorder:
    """Per-phase wrapper around :class:`BudgetTracker` for the
    model axes.

    Parameters
    ----------
    tracker
        The underlying :class:`BudgetTracker`. The recorder
        forwards every write to it; ``check`` and ``enforce``
        return the same decisions as if the underlying tracker
        were used directly.
    phase_axis_filter
        Optional override of the per-phase axes. Defaults to
        :data:`PER_PHASE_MODEL_AXES` so the recorder is
        opinionated about being used for model-axis tracking.

    Notes
    -----
    The recorder does NOT raise on budget exhaustion itself;
    it forwards writes to the underlying tracker and lets the
    tracker decide. This keeps the cluster WP8 enforcement
    path single-sourced.
    """

    tracker: BudgetTracker
    phase_axis_filter: frozenset[BudgetAxis] = field(
        default=PER_PHASE_MODEL_AXES,
    )
    _phase_records: dict[str, dict[BudgetAxis, float]] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        self._phase_records = {}

    def record(
        self,
        *,
        phase_id: str,
        axis: BudgetAxis,
        value: float,
    ) -> None:
        """Record consumption for one phase on one axis.

        The write is forwarded to the underlying
        ``BudgetTracker`` so enforcement stays single-sourced.
        Only axes in :data:`phase_axis_filter` are added to the
        per-phase breakdown; recording an off-filter axis
        raises ``ValueError`` so a misconfigured call site is
        caught immediately.
        """
        if value < 0:
            msg = f"phase consumption must be >= 0, got {value}"
            raise ValueError(msg)
        if not phase_id or not phase_id.strip():
            raise ValueError("phase_id must be a non-empty string")
        if axis not in self.phase_axis_filter:
            msg = (
                f"axis {axis.value!r} is not in the per-phase filter "
                f"{sorted(a.value for a in self.phase_axis_filter)}; "
                f"use the underlying BudgetTracker for non-model axes"
            )
            raise ValueError(msg)
        # Forward to the underlying tracker. The recorder is a
        # thin shim; enforcement stays single-sourced.
        self.tracker.record(axis, value)
        # Mirror into the per-phase map.
        bucket = self._phase_records.setdefault(phase_id, {})
        bucket[axis] = bucket.get(axis, 0.0) + float(value)

    def record_invocation(
        self,
        *,
        phase_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        elapsed_s: float,
    ) -> None:
        """Convenience: record a complete invocation against
        the three model axes.

        Rejects negative values defensively (the upstream
        ``ModelUsage`` model already enforces ``ge=0`` but the
        recorder is defensive at the boundary).
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError(
                f"tokens must be >= 0, got input={input_tokens}, output={output_tokens}"
            )
        if cost_usd < 0:
            raise ValueError(f"cost_usd must be >= 0, got {cost_usd}")
        if elapsed_s < 0:
            raise ValueError(f"elapsed_s must be >= 0, got {elapsed_s}")
        self.record(
            phase_id=phase_id,
            axis=BudgetAxis.MODEL_TOKENS,
            value=float(input_tokens + output_tokens),
        )
        self.record(
            phase_id=phase_id,
            axis=BudgetAxis.MODEL_COST_USD,
            value=cost_usd,
        )
        self.record(
            phase_id=phase_id,
            axis=BudgetAxis.ELAPSED_SECONDS,
            value=elapsed_s,
        )

    def consumption_by_phase(
        self,
    ) -> Mapping[str, Mapping[BudgetAxis, float]]:
        """Read-only view of per-phase consumption.

        The outer mapping is phase_id \u2192 inner mapping; the
        inner mapping is axis \u2192 amount. Phases are reported
        in insertion order.
        """
        return {phase_id: dict(axes) for phase_id, axes in self._phase_records.items()}

    def phases(self) -> tuple[str, ...]:
        """Return recorded phase IDs in insertion order."""
        return tuple(self._phase_records.keys())

    def consumption_for_phase(
        self,
        phase_id: str,
    ) -> Mapping[BudgetAxis, float]:
        """Return consumption for a single phase; empty
        mapping when the phase hasn't recorded anything."""
        return dict(self._phase_records.get(phase_id, {}))

    def check(self) -> BudgetDecision:
        """Forward to underlying tracker's check."""
        return self.tracker.check()

    def enforce(self) -> BudgetDecision:
        """Forward to underlying tracker's enforce; raises
        :class:`BudgetExhausted` when an axis is exhausted."""
        return self.tracker.enforce()

    def underlying_tracker(self) -> BudgetTracker:
        """Expose the underlying tracker for callers that
        need the full consumption map (not just per-phase)."""
        return self.tracker


def build_recorder(
    *,
    budgets: RunBudgets,
    tracker: BudgetTracker | None = None,
) -> PerPhaseBudgetRecorder:
    """Convenience: build a recorder with a fresh tracker (or
    an injected one)."""
    if tracker is None:
        tracker = BudgetTracker(budgets=budgets)
    return PerPhaseBudgetRecorder(tracker=tracker)


def persist_by_phase(
    recorder: PerPhaseBudgetRecorder,
    *,
    run_dir: Path,
) -> Path:
    """Persist the per-phase breakdown to ``<run_dir>/budget/
    by-phase.json``.

    The JSON shape is::

        {
          "budgets_ceiling": {"model_tokens": 10000, ...},
          "by_phase": {
            "spec": {"model_tokens": 100, "model_cost_usd": 0.002,
                      "elapsed_seconds": 0.5},
            "plan": {"model_tokens": 250, "model_cost_usd": 0.005,
                      "elapsed_seconds": 1.2},
            ...
          }
        }

    The orchestrator calls this on each phase boundary so the
    dashboard has fresh data after each phase. Returns the
    resolved file path.
    """
    run_dir = Path(run_dir)
    budget_dir = run_dir / "budget"
    budget_dir.mkdir(parents=True, exist_ok=True)
    path = budget_dir / "by-phase.json"
    # Build serialisable form.
    ceilings: dict[str, float] = {}
    for axis, ceiling in (
        (BudgetAxis.MODEL_TOKENS, recorder.tracker.budgets.model_tokens),
        (BudgetAxis.MODEL_COST_USD, recorder.tracker.budgets.model_cost_usd),
        (BudgetAxis.ELAPSED_SECONDS, recorder.tracker.budgets.elapsed_seconds),
    ):
        if ceiling is not None:
            ceilings[axis.value] = float(ceiling)
    payload = {
        "budgets_ceiling": ceilings,
        "by_phase": {
            phase_id: {axis.value: amount for axis, amount in axes.items()}
            for phase_id, axes in recorder.consumption_by_phase().items()
        },
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


def load_by_phase(path: Path) -> dict[str, object]:
    """Read the persisted per-phase breakdown back into memory.

    Used by the dashboard; tests use it to verify the persist
    step roundtrips."""
    payload: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"by-phase.json expected object, got {type(payload).__name__}"
        raise ValueError(msg)
    return payload


__all__ = [
    "PER_PHASE_MODEL_AXES",
    "PerPhaseBudgetRecorder",
    "build_recorder",
    "load_by_phase",
    "persist_by_phase",
]
