"""Cluster P1+P2: per-phase and per-task model-axis budget
tracking tests.

Pins the deferred follow-up: per-axis model token/cost tracking
at the services layer.

Cluster P1 coverage
-------------------

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

Cluster P2 coverage
-------------------

- task_id is required on record(); missing or whitespace task_id
  raises ValueError.
- Different tasks stay isolated -- task A does not leak into B.
- Two phases driving the SAME task accumulate into that task's
  bucket (the per-task view is a fine-grained projection of the
  same writes, not a separate stream).
- The per-phase view and the per-task view always reconcile to
  the underlying tracker's totals.
- persist_by_task writes <run_dir>/budget/by-task.json with the
  same envelope shape as by-phase.json.
- load_by_task roundtrips and validates the top-level key.
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
    load_by_task,
    persist_by_phase,
    persist_by_task,
)

# Cluster P1 + P2 fixtures use simple, descriptive IDs so the
# tests read like a spec of the recorder contract.
_SPEC = "spec"
_PLAN = "plan"
_IMPL = "implement"
_TASK_FOO = "task-foo"
_TASK_BAR = "task-bar"
_TASK_BAZ = "task-baz"


# ---------------------------------------------------------------------------
# Per-phase isolation (Cluster P1)
# ---------------------------------------------------------------------------


class TestPerPhaseIsolation:
    def test_two_phases_dont_leak(self) -> None:
        budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=500,
        )
        recorder.record(
            phase_id=_PLAN,
            task_id=_TASK_BAR,
            axis=BudgetAxis.MODEL_TOKENS,
            value=250,
        )
        breakdown = recorder.consumption_by_phase()
        assert breakdown[_SPEC][BudgetAxis.MODEL_TOKENS] == 500.0
        assert breakdown[_PLAN][BudgetAxis.MODEL_TOKENS] == 250.0
        # Underlying tracker sees both (the total).
        assert recorder.tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 750.0

    def test_three_phases_aggregate_to_underlying_total(self) -> None:
        budgets = RunBudgets(model_tokens=10_000)
        recorder = build_recorder(budgets=budgets)
        for pid in (_SPEC, _PLAN, _IMPL):
            recorder.record(
                phase_id=pid,
                task_id=f"task-{pid}",
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
                task_id=f"task-{pid}",
                axis=BudgetAxis.MODEL_TOKENS,
                value=1,
            )
        assert recorder.phases() == ("plan", "spec", "implement", "remediate")

    def test_unrecorded_phase_returns_empty_mapping(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=10,
        )
        assert recorder.consumption_for_phase("plan") == {}


# ---------------------------------------------------------------------------
# Per-task isolation (Cluster P2)
# ---------------------------------------------------------------------------


class TestPerTaskIsolation:
    def test_two_tasks_dont_leak(self) -> None:
        budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=500,
        )
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_BAR,
            axis=BudgetAxis.MODEL_TOKENS,
            value=250,
        )
        breakdown = recorder.consumption_by_task()
        assert breakdown[_TASK_FOO][BudgetAxis.MODEL_TOKENS] == 500.0
        assert breakdown[_TASK_BAR][BudgetAxis.MODEL_TOKENS] == 250.0
        # Underlying tracker sees both (the total).
        assert recorder.tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 750.0

    def test_three_tasks_aggregate_to_underlying_total(self) -> None:
        budgets = RunBudgets(model_tokens=10_000)
        recorder = build_recorder(budgets=budgets)
        for tid in (_TASK_FOO, _TASK_BAR, _TASK_BAZ):
            recorder.record(
                phase_id=_SPEC,
                task_id=tid,
                axis=BudgetAxis.MODEL_TOKENS,
                value=100,
            )
        breakdown = recorder.consumption_by_task()
        assert sum(axes[BudgetAxis.MODEL_TOKENS] for axes in breakdown.values()) == 300.0
        assert recorder.tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 300.0

    def test_tasks_appear_in_insertion_order(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        for tid in (_TASK_BAR, _TASK_FOO, _TASK_BAZ):
            recorder.record(
                phase_id=_SPEC,
                task_id=tid,
                axis=BudgetAxis.MODEL_TOKENS,
                value=1,
            )
        assert recorder.tasks() == (_TASK_BAR, _TASK_FOO, _TASK_BAZ)

    def test_unrecorded_task_returns_empty_mapping(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=10,
        )
        assert recorder.consumption_for_task(_TASK_BAR) == {}

    def test_two_phases_same_task_accumulate(self) -> None:
        """A task may be executed across multiple phases (e.g.
        ``spec → plan → implement``). The per-task bucket
        must accumulate regardless of which phase recorded it.
        """
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=100,
        )
        recorder.record(
            phase_id=_IMPL,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=250,
        )
        # Per-task view sees the sum.
        task_foo = recorder.consumption_for_task(_TASK_FOO)
        assert task_foo[BudgetAxis.MODEL_TOKENS] == 350.0
        # Per-phase view sees each phase independently.
        assert recorder.consumption_for_phase(_SPEC)[BudgetAxis.MODEL_TOKENS] == 100.0
        assert recorder.consumption_for_phase(_IMPL)[BudgetAxis.MODEL_TOKENS] == 250.0

    def test_per_phase_and_per_task_views_reconcile_to_total(self) -> None:
        """The two breakdowns are projections of the same
        writes; both must sum to the underlying tracker total.
        """
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_COST_USD,
            value=0.010,
        )
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_BAR,
            axis=BudgetAxis.MODEL_COST_USD,
            value=0.020,
        )
        recorder.record(
            phase_id=_IMPL,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_COST_USD,
            value=0.030,
        )
        # Per-phase sum == per-task sum == underlying total.
        per_phase_sum = sum(
            axes[BudgetAxis.MODEL_COST_USD] for axes in recorder.consumption_by_phase().values()
        )
        per_task_sum = sum(
            axes[BudgetAxis.MODEL_COST_USD] for axes in recorder.consumption_by_task().values()
        )
        underlying = recorder.tracker.consumption()[BudgetAxis.MODEL_COST_USD]
        assert per_phase_sum == 0.060
        assert per_task_sum == 0.060
        assert underlying == 0.060


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
                phase_id=_SPEC,
                task_id=_TASK_FOO,
                axis=axis,
                value=1.0,
            )

    def test_in_filter_axes_accepted(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        for axis in PER_PHASE_MODEL_AXES:
            recorder.record(
                phase_id=_SPEC,
                task_id=_TASK_FOO,
                axis=axis,
                value=10,
            )
        breakdown = recorder.consumption_by_phase()
        assert set(breakdown[_SPEC].keys()) == PER_PHASE_MODEL_AXES

    def test_custom_filter_overrides_default(self) -> None:
        budgets = RunBudgets()
        tracker = BudgetTracker(budgets=budgets)
        recorder = PerPhaseBudgetRecorder(
            tracker=tracker,
            phase_axis_filter=frozenset({BudgetAxis.MODEL_TOKENS}),
        )
        with pytest.raises(ValueError, match="not in the per-phase filter"):
            recorder.record(
                phase_id=_SPEC,
                task_id=_TASK_FOO,
                axis=BudgetAxis.MODEL_COST_USD,
                value=0.01,
            )
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=100,
        )
        assert recorder.consumption_by_phase()[_SPEC][BudgetAxis.MODEL_TOKENS] == 100.0


# ---------------------------------------------------------------------------
# Defensive validation
# ---------------------------------------------------------------------------


class TestDefensiveValidation:
    def test_negative_value_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match=">= 0"):
            recorder.record(
                phase_id=_SPEC,
                task_id=_TASK_FOO,
                axis=BudgetAxis.MODEL_TOKENS,
                value=-1,
            )

    def test_empty_phase_id_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="phase_id"):
            recorder.record(
                phase_id="",
                task_id=_TASK_FOO,
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_whitespace_phase_id_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="phase_id"):
            recorder.record(
                phase_id="   ",
                task_id=_TASK_FOO,
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_empty_task_id_rejected(self) -> None:
        """Cluster P2: ``record()`` now requires task_id. A
        missing or whitespace task_id is a programmer error
        (callers must commit to which task the write belongs
        to), so we raise rather than silently defaulting.
        """
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="task_id"):
            recorder.record(
                phase_id=_SPEC,
                task_id="",
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_whitespace_task_id_rejected(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="task_id"):
            recorder.record(
                phase_id=_SPEC,
                task_id="   ",
                axis=BudgetAxis.MODEL_TOKENS,
                value=10,
            )

    def test_record_invocation_rejects_negative_tokens(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="tokens must be >= 0"):
            recorder.record_invocation(
                phase_id=_SPEC,
                task_id=_TASK_FOO,
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
                phase_id=_SPEC,
                task_id=_TASK_FOO,
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
                phase_id=_SPEC,
                task_id=_TASK_FOO,
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.01,
                elapsed_s=-0.1,
            )

    def test_record_invocation_rejects_empty_task_id(self) -> None:
        """Cluster P2: ``record_invocation()`` must also
        validate task_id at the boundary.
        """
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        with pytest.raises(ValueError, match="task_id"):
            recorder.record_invocation(
                phase_id=_SPEC,
                task_id="",
                input_tokens=10,
                output_tokens=10,
                cost_usd=0.01,
                elapsed_s=0.5,
            )


# ---------------------------------------------------------------------------
# record_invocation helper
# ---------------------------------------------------------------------------


class TestRecordInvocationHelper:
    def test_records_all_three_axes(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        axes = recorder.consumption_for_phase(_SPEC)
        assert axes[BudgetAxis.MODEL_TOKENS] == 300.0
        assert axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert axes[BudgetAxis.ELAPSED_SECONDS] == 0.5
        # Cluster P2: same write must also land in the
        # per-task bucket.
        task_axes = recorder.consumption_for_task(_TASK_FOO)
        assert task_axes[BudgetAxis.MODEL_TOKENS] == 300.0
        assert task_axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert task_axes[BudgetAxis.ELAPSED_SECONDS] == 0.5

    def test_two_invocations_aggregate_within_phase(self) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_PLAN,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.003,
            elapsed_s=0.5,
        )
        recorder.record_invocation(
            phase_id=_PLAN,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.003,
            elapsed_s=0.5,
        )
        axes = recorder.consumption_for_phase(_PLAN)
        assert axes[BudgetAxis.MODEL_TOKENS] == 600.0
        assert axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert axes[BudgetAxis.ELAPSED_SECONDS] == 1.0
        # Same accumulation visible in the per-task view.
        task_axes = recorder.consumption_for_task(_TASK_FOO)
        assert task_axes[BudgetAxis.MODEL_TOKENS] == 600.0
        assert task_axes[BudgetAxis.MODEL_COST_USD] == 0.006
        assert task_axes[BudgetAxis.ELAPSED_SECONDS] == 1.0


# ---------------------------------------------------------------------------
# Enforcement forward - cluster WP8 invariants preserved
# ---------------------------------------------------------------------------


class TestEnforcementForwarded:
    def test_check_returns_same_decision_as_underlying(self) -> None:
        budgets = RunBudgets(model_tokens=1000)
        tracker = BudgetTracker(budgets=budgets)
        recorder = PerPhaseBudgetRecorder(tracker=tracker)
        recorder.record(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=200,
        )
        recorder.record(
            phase_id=_PLAN,
            task_id=_TASK_BAR,
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
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.MODEL_TOKENS,
            value=1500,
        )
        with pytest.raises(BudgetExhausted) as excinfo:
            recorder.enforce()
        assert excinfo.value.decision.outcome == BudgetOutcome.BLOCKED


# ---------------------------------------------------------------------------
# Persistence (by-phase.json) — Cluster P1
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
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        recorder.record_invocation(
            phase_id=_PLAN,
            task_id=_TASK_BAR,
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
            phase_id=_SPEC,
            task_id=_TASK_FOO,
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
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        path = persist_by_phase(recorder, run_dir=tmp_path / "run")
        payload = load_by_phase(path)
        assert payload["budgets_ceiling"]["model_tokens"] == 10_000.0
        spec = payload["by_phase"][_SPEC]
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
# Persistence (by-task.json) — Cluster P2
# ---------------------------------------------------------------------------


class TestPersistByTask:
    def test_writes_path_under_run_dir(self, tmp_path: Path) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        recorder.record_invocation(
            phase_id=_IMPL,
            task_id=_TASK_BAR,
            input_tokens=250,
            output_tokens=100,
            cost_usd=0.007,
            elapsed_s=1.0,
        )
        path = persist_by_task(recorder, run_dir=tmp_path / "run")
        assert path == tmp_path / "run" / "budget" / "by-task.json"
        assert path.exists()

    def test_creates_nested_run_dir(self, tmp_path: Path) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.001,
            elapsed_s=0.01,
        )
        deep = tmp_path / "a" / "b" / "c" / "run"
        path = persist_by_task(recorder, run_dir=deep)
        assert path.exists()

    def test_load_roundtrip(self, tmp_path: Path) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        path = persist_by_task(recorder, run_dir=tmp_path / "run")
        payload = load_by_task(path)
        assert payload["budgets_ceiling"]["model_tokens"] == 10_000.0
        task_foo = payload["by_task"][_TASK_FOO]
        assert task_foo["model_tokens"] == 300.0
        assert task_foo["model_cost_usd"] == 0.006
        assert task_foo["elapsed_seconds"] == 0.5

    def test_load_empty_when_nothing_recorded(self, tmp_path: Path) -> None:
        budgets = RunBudgets()
        recorder = build_recorder(budgets=budgets)
        path = persist_by_task(recorder, run_dir=tmp_path / "run")
        payload = load_by_task(path)
        assert payload["by_task"] == {}

    def test_by_phase_and_by_task_appear_in_same_run_dir(self, tmp_path: Path) -> None:
        """Cluster P2 contracts: both artifacts live in the
        same ``<run_dir>/budget/`` directory and share the
        ``budgets_ceiling`` envelope.
        """
        budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
        recorder = build_recorder(budgets=budgets)
        recorder.record_invocation(
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.006,
            elapsed_s=0.5,
        )
        run_dir = tmp_path / "run"
        phase_path = persist_by_phase(recorder, run_dir=run_dir)
        task_path = persist_by_task(recorder, run_dir=run_dir)
        assert phase_path.parent == task_path.parent == run_dir / "budget"
        # Shared envelope ceilings.
        phase_payload = load_by_phase(phase_path)
        task_payload = load_by_task(task_path)
        assert phase_payload["budgets_ceiling"] == task_payload["budgets_ceiling"]

    def test_load_by_task_rejects_non_object_payload(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError, match="expected object"):
            load_by_task(bad)

    def test_load_by_task_rejects_missing_top_level_key(self, tmp_path: Path) -> None:
        """If a file has the by-phase envelope
        (``by_phase``) but no ``by_task`` key, the loader
        for the per-task artifact refuses it. This catches
        a class of dashboard wiring bugs where the wrong
        file gets passed to the wrong loader.
        """
        wrong = tmp_path / "wrong.json"
        wrong.write_text(
            '{"budgets_ceiling": {}, "by_phase": {}}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="by_task"):
            load_by_task(wrong)


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
            phase_id=_SPEC,
            task_id=_TASK_FOO,
            axis=BudgetAxis.ELAPSED_SECONDS,
            value=0.5,
        )
        assert recorder.tracker.consumption()[BudgetAxis.ELAPSED_SECONDS] == 2.0

    def test_by_phase_and_by_task_independent_when_no_task_writes(self) -> None:
        """If a recorder is only used via the underlying
        tracker (no ``record()`` calls), both views are
        empty but the recorder is still functional.
        """
        budgets = RunBudgets()
        tracker = BudgetTracker(budgets=budgets)
        recorder = PerPhaseBudgetRecorder(tracker=tracker)
        assert recorder.consumption_by_phase() == {}
        assert recorder.consumption_by_task() == {}
