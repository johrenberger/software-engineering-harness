"""Cluster P1: per-phase model-axis budget tracking tests.

Pins the deferred follow-up: per-axis model token/cost tracking
at the services layer.

Coverage:

- PerPhaseBudgetRecorder records per-phase consumption on the
  model axes; totals match the underlying BudgetTracker.
- Different phases stay isolated -- phase A does not leak into B.
- Recording an axis outside the model-axis filter raises ValueError.
- Negative values are rejected.
- Empty phase_id rejected.
- consumption_by_phase() returns insertion-ordered view.
- consumption_for_phase() returns empty for an unrecorded phase.
- check() and enforce() forward to the underlying tracker.
- persist_by_phase writes <run_dir>/budget/by-phase.json.
- record_invocation records a complete call against all three
  model axes in one call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.orchestrator.budgets import (
    BudgetAxis,
    BudgetExhausted,
    BudgetOutcome,
    BudgetTracker,
    RunBudgets,
)
from seharness.orchestrator.per_phase_budget import (
    PER_PHASE_MODEL_AXES,
    PerPhaseBudgetRecorder,
    build_recorder,
    load_by_phase,
    persist_by_phase,
)

# ---------------------------------------------------------------------------
# Per-phase isolation
# ---------------------------------------------------------------------------


class TestPerPhaseIsolation:
    def test_two_phases_dont_leak(self) -> None:
        budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.MODEL_TOKENS,
            value=500,
        )
        recorder.record(
            phase_id="plan",
            axis=BudgetAxis.MODEL_TOKENS,
            value=250,
        )
        breakdown = recorder.consumption_by_phase()
        assert breakdown["spec"][BudgetAxis.MODEL_TOKENS] == 500.0
        assert breakdown["plan"][BudgetAxis.MODEL_TOKENS] == 250.0
        # Underlying tracker sees both (the total).
        assert recorder.tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 750.0

    def test_three_phases_aggregate_to_underlying_total(self) -> None:
        budgets = RunBudgets(model_tokens=10_000)
        recorder = build_recorder(budgets=budgets)
        for pid in ("spec", "plan", "implement"):
            recorder.record(
                phase_id=pid,
                axis=BudgetAxis.MODEL_TOKENS,
                value=100,
            )
        breakdown = recorder.consumption_by_phase()
        assert sum(axes[BudgetAxis.MODEL_TOKENS] for axes in breakdown.values()) == 300.0
        assert recorder.tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 300.0

    def test_phases_appear_in_insertion_order(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        for pid in ("plan", "spec", "implement", "remediate"):
            recorder.record(
                phase_id=pid,
                axis=BudgetAxis.MODEL_TOKENS,
                value=1,
            )
        assert recorder.phases() == ("plan", "spec", "implement", "remediate")

    def test_unrecorded_phase_returns_empty_mapping(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.MODEL_TOKENS,
            value=10,
        )
        assert recorder.consumption_for_phase("plan") == {}


# ---------------------------------------------------------------------------
# Axis filter enforcement
# ---------------------------------------------------------------------------


class TestAxisFilterEnforcement:
    @pytest.mark.parametrize("axis", list(BudgetAxis))
    def test_off_filter_axis_raises(self, axis: BudgetAxis) -> None:
        if axis in PER_PHASE_MODEL_AXES:
            pytest.skip("axis is in the model filter")
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="not in the per-phase filter"):
            recorder.record(
                phase_id="spec",
                axis=axis,
                value=1.0,
            )

    def test_in_filter_axes_accepted(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        for axis in PER_PHASE_MODEL_AXES:
            recorder.record(phase_id="spec", axis=axis, value=10)
        breakdown = recorder.consumption_by_phase()
        assert set(breakdown["spec"].keys()) == PER_PHASE_MODEL_AXES

    def test_custom_filter_overrides_default(self) -> None:
        budgets = RunBudgets()
        tracker = BudgetTracker(budgets=budgets)
        recorder = PerPhaseBudgetRecorder(
            tracker=tracker,
            phase_axis_filter=frozenset({BudgetAxis.MODEL_TOKENS}),
        )
        with pytest.raises(ValueError, match="not in the per-phase filter"):
            recorder.record(
                phase_id="spec",
                axis=BudgetAxis.MODEL_COST_USD,
                value=0.01,
            )
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.MODEL_TOKENS,
            value=100,
        )
        assert recorder.consumption_by_phase()["spec"][BudgetAxis.MODEL_TOKENS] == 100.0


# ---------------------------------------------------------------------------
# Defensive validation
# ---------------------------------------------------------------------------


class TestDefensiveValidation:
    def test_negative_value_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match=">= 0"):
            recorder.record(
                phase_id="spec",
                axis=BudgetAxis.MODEL_TOKENS,
                value=-1,
            )

    def test_empty_phase_id_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="non-empty"):
            recorder.record(
                phase_id="",
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_whitespace_phase_id_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="non-empty"):
            recorder.record(
                phase_id="   ",
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_record_invocation_rejects_negative_tokens(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="tokens must be >= 0"):
            recorder.record_invocation(
                phase_id="spec",
                input_tokens=-1,
                output_tokens=10,
                cost_usd=0.01,
                elapsed_s=0.5,
            )

    def test_record_invocation_rejects_negative_cost(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="cost_usd"):
            recorder.record_invocation(
                phase_id="spec",
                input_tokens=10,
                output_tokens=10,
                cost_usd=-0.01,
                elapsed_s=0.5,
            )

    def test_record_invocation_rejects_negative_elapsed(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="elapsed_s"):
            recorder.record_invocation(
                phase_id="spec",
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.01,
                elapsed_s=-0.1,
            )


# ---------------------------------------------------------------------------
# record_invocation helper
# ---------------------------------------------------------------------------


class TestRecordInvocationHelper:
    def test_records_all_three_axes(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id="spec",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        axes = recorder.consumption_for_phase("spec")
        assert axes[BudgetAxis.MODEL_TOKENS] == 300.0
        assert axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert axes[BudgetAxis.ELAPSED_SECONDS] == 0.5

    def test_two_invocations_aggregate_within_phase(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id="plan",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.003,
            elapsed_s=0.5,
        )
        recorder.record_invocation(
            phase_id="plan",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.003,
            elapsed_s=0.5,
        )
        axes = recorder.consumption_for_phase("plan")
        assert axes[BudgetAxis.MODEL_TOKENS] == 600.0
        assert axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert axes[BudgetAxis.ELAPSED_SECONDS] == 1.0


# ---------------------------------------------------------------------------
# Enforcement forward - cluster WP8 invariants preserved
# ---------------------------------------------------------------------------


class TestEnforcementForwarded:
    def test_check_returns_same_decision_as_underlying(self) -> None:
        budgets = RunBudgets(model_tokens=1000)
        tracker = BudgetTracker(budgets=budgets)
        recorder = PerPhaseBudgetRecorder(tracker=tracker)
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.MODEL_TOKENS,
            value=200,
        )
        recorder.record(
            phase_id="plan",
            axis=BudgetAxis.MODEL_TOKENS,
            value=900,
        )
        decision = recorder.check()
        assert decision.outcome == BudgetOutcome.BLOCKED
        assert decision.exceeded_axis == BudgetAxis.MODEL_TOKENS

    def test_enforce_raises_budget_exhausted(self) -> None:
        budgets = RunBudgets(model_tokens=1000)
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.MODEL_TOKENS,
            value=1500,
        )
        with pytest.raises(BudgetExhausted) as excinfo:
            recorder.enforce()
        assert excinfo.value.decision.outcome == BudgetOutcome.BLOCKED


# ---------------------------------------------------------------------------
# Persistence (by-phase.json)
# ---------------------------------------------------------------------------


class TestPersistByPhase:
    def test_writes_path_under_run_dir(self, tmp_path: Path) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id="spec",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        recorder.record_invocation(
            phase_id="plan",
            input_tokens=250,
            output_tokens=100,
            cost_usd=0.007,
            elapsed_s=1.0,
        )
        path = persist_by_phase(recorder, run_dir=tmp_path / "run")
        assert path == tmp_path / "run" / "budget" / "by-phase.json"
        assert path.exists()

    def test_creates_nested_run_dir(self, tmp_path: Path) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id="spec",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.001,
            elapsed_s=0.01,
        )
        deep = tmp_path / "a" / "b" / "c" / "run"
        path = persist_by_phase(recorder, run_dir=deep)
        assert path.exists()

    def test_load_roundtrip(self, tmp_path: Path) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id="spec",
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        path = persist_by_phase(recorder, run_dir=tmp_path / "run")
        payload = load_by_phase(path)
        assert payload["budgets_ceiling"]["model_tokens"] == 10_000.0
        spec = payload["by_phase"]["spec"]
        assert spec["model_tokens"] == 300.0
        assert spec["model_cost_usd"] == 0.006
        assert spec["elapsed_seconds"] == 0.5

    def test_load_empty_when_nothing_recorded(self, tmp_path: Path) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        path = persist_by_phase(recorder, run_dir=tmp_path / "run")
        payload = load_by_phase(path)
        assert payload["by_phase"] == {}


# ---------------------------------------------------------------------------
# Default convenience constructor
# ---------------------------------------------------------------------------


class TestBuildRecorder:
    def test_default_filter_is_model_axes(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        assert recorder.phase_axis_filter == PER_PHASE_MODEL_AXES

    def test_injects_inner_tracker(self) -> None:
        budgets = RunBudgets()
        tracker = BudgetTracker(budgets=budgets)
        recorder = build_recorder(budgets=budgets, tracker=tracker)
        assert recorder.underlying_tracker() is tracker

    def test_creates_inner_tracker_when_none(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        assert recorder.tracker.budgets is budgets


# ---------------------------------------------------------------------------
# Backwards compatibility
# ---------------------------------------------------------------------------


class TestBackwardsCompatibility:
    def test_underlying_tracker_unchanged(self) -> None:
        budgets = RunBudgets(elapsed_seconds=60.0)
        tracker = BudgetTracker(budgets=budgets)
        tracker.record(BudgetAxis.ELAPSED_SECONDS, 1.5)
        recorder = PerPhaseBudgetRecorder(tracker=tracker)
        assert recorder.tracker.consumption()[BudgetAxis.ELAPSED_SECONDS] == 1.5
        recorder.record(
            phase_id="spec",
            axis=BudgetAxis.ELAPSED_SECONDS,
            value=0.5,
        )
        assert recorder.tracker.consumption()[BudgetAxis.ELAPSED_SECONDS] == 2.0
