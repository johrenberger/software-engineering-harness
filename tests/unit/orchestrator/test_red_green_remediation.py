"""Cluster N PR6 \u2014 red-green-remediation cycle tests.

Pins the workplan Step 6 exit criterion: both direct-success
and one-remediation paths complete with genuine command
evidence.

The tests cover:

- :class:`MiniMaxBudgetTracker` records tokens, cost, and
  elapsed against a narrower axis surface; rejects axes
  outside the surface; raises ``BudgetExhausted`` when a
  ceiling is hit.
- :func:`run_red_green_cycle` runs end-to-end against a
  deterministic fixture: first implementation is broken,
  validation returns structured evidence, remediation
  patch is applied (via the deterministic parser), and
  final validation passes.
- :func:`persist_cycle_result` writes the outcome JSON
  under ``<run_dir>``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.domain.enums import ProviderName
from seharness.domain.results import ModelResponse, ModelUsage
from seharness.orchestrator.budgets import (
    BudgetAxis,
    BudgetExhausted,
    BudgetOutcome,
    RunBudgets,
)
from seharness.orchestrator.minimax_budget_tracker import (
    DEFAULT_COST_PER_1K_TOKENS,
    MiniMaxBudgetTracker,
)
from seharness.orchestrator.red_green_cycle import (
    CommandResult,
    ValidationEvidence,
    persist_cycle_result,
    run_red_green_cycle,
)


def _ok_response(*, text: str = "ok") -> ModelResponse:
    return ModelResponse(
        provider=ProviderName.MINIMAX,
        model="MiniMax-M2.7",
        raw_output=text,
        parsed={"text": text},
        usage=ModelUsage(input_tokens=100, output_tokens=200),
        error=None,
        duration_s=0.5,
    )


class _FakeValidationRunner:
    """In-process validation runner that flips between RED and
    GREEN based on the call count.

    The first invocation returns exit_code=1 (broken); every
    subsequent invocation returns exit_code=0 (passing). This
    simulates the workplan's controlled failure fixture: the
    first validation fails (RED), the remediation patch is
    applied, the second validation passes (GREEN).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, command: str) -> CommandResult:
        self.calls.append(command)
        if len(self.calls) == 1:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr="1 failed",
                duration_s=0.1,
            )
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="all tests passed",
            stderr="",
            duration_s=0.1,
        )


class _StaticRepairParser:
    """Repair parser that returns a known-good fixture patch.

    Workplan: 'One remediation patch corrects the defect.'"""

    def __init__(self, patch: str) -> None:
        self._patch = patch

    def __call__(self, response: ModelResponse) -> str | None:
        return self._patch


class _NoneRepairParser:
    """Repair parser that returns ``None`` (no patch)."""

    def __call__(self, response: ModelResponse) -> str | None:
        return None


# ---------------------------------------------------------------------------
# MiniMaxBudgetTracker
# ---------------------------------------------------------------------------


class TestMiniMaxBudgetTrackerRecordTokens:
    """The tracker records tokens + cost against the model
    axis. Reject negative token counts (defensive at the
    boundary; the upstream ModelUsage model already enforces
    ``ge=0``)."""

    def test_records_tokens_and_cost(self) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_tokens(input_tokens=4000, output_tokens=1000)
        # 5000 tokens / 1000 * 0.002 = 0.01 USD
        consumption = tracker.consumption()
        assert consumption[BudgetAxis.MODEL_TOKENS] == 5000.0
        assert consumption[BudgetAxis.MODEL_COST_USD] == pytest.approx(0.01)

    def test_rejects_negative_input_tokens(self) -> None:
        budgets = RunBudgets()
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        with pytest.raises(ValueError, match="input_tokens"):
            tracker.record_tokens(input_tokens=-1, output_tokens=10)

    def test_rejects_negative_output_tokens(self) -> None:
        budgets = RunBudgets()
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        with pytest.raises(ValueError, match="output_tokens"):
            tracker.record_tokens(input_tokens=10, output_tokens=-1)

    def test_rejects_negative_cost_rate(self) -> None:
        budgets = RunBudgets()
        with pytest.raises(ValueError, match="cost_per_1k_tokens"):
            MiniMaxBudgetTracker(budgets=budgets, cost_per_1k_tokens=-0.01)


class TestMiniMaxBudgetTrackerRecordElapsed:
    def test_records_elapsed(self) -> None:
        budgets = RunBudgets(elapsed_seconds=60.0)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_elapsed(2.5)
        assert tracker.consumption()[BudgetAxis.ELAPSED_SECONDS] == 2.5

    def test_rejects_negative_duration(self) -> None:
        budgets = RunBudgets()
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        with pytest.raises(ValueError, match="duration_s"):
            tracker.record_elapsed(-0.1)


class TestMiniMaxBudgetTrackerRecordInvocation:
    def test_records_from_response(self) -> None:
        budgets = RunBudgets(
            model_tokens=10_000,
            model_cost_usd=1.0,
            elapsed_seconds=60.0,
        )
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        response = _ok_response()  # 100 in, 200 out, 0.5s
        tracker.record_invocation(response)
        consumption = tracker.consumption()
        assert consumption[BudgetAxis.MODEL_TOKENS] == 300.0
        assert consumption[BudgetAxis.MODEL_COST_USD] == pytest.approx(
            300.0 / 1000.0 * DEFAULT_COST_PER_1K_TOKENS
        )
        assert consumption[BudgetAxis.ELAPSED_SECONDS] == 0.5

    def test_handles_response_without_usage(self) -> None:
        budgets = RunBudgets()
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        response = ModelResponse(
            provider=ProviderName.MINIMAX,
            model="MiniMax-M2.7",
            raw_output="ok",
            parsed=None,
            usage=None,
            error=None,
            duration_s=0.5,
        )
        tracker.record_invocation(response)
        # Only elapsed recorded; no token / cost consumption.
        consumption = tracker.consumption()
        assert BudgetAxis.MODEL_TOKENS not in consumption
        assert BudgetAxis.MODEL_COST_USD not in consumption
        assert consumption[BudgetAxis.ELAPSED_SECONDS] == 0.5


class TestMiniMaxBudgetTrackerEnforcement:
    def test_enforce_raises_when_tokens_exceeded(self) -> None:
        budgets = RunBudgets(model_tokens=1000)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_tokens(input_tokens=800, output_tokens=300)
        with pytest.raises(BudgetExhausted) as excinfo:
            tracker.enforce()
        assert excinfo.value.decision.outcome == BudgetOutcome.BLOCKED
        assert excinfo.value.decision.exceeded_axis == BudgetAxis.MODEL_TOKENS

    def test_check_is_read_only(self) -> None:
        budgets = RunBudgets(model_tokens=1000)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_tokens(input_tokens=800, output_tokens=300)
        decision = tracker.check()
        assert decision.outcome == BudgetOutcome.BLOCKED
        # Re-checking should yield the same decision without
        # raising; check() is read-only.
        decision2 = tracker.check()
        assert decision2.outcome == BudgetOutcome.BLOCKED

    def test_enforce_passes_under_ceiling(self) -> None:
        budgets = RunBudgets(model_tokens=10_000)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_tokens(input_tokens=100, output_tokens=200)
        decision = tracker.enforce()
        assert decision.outcome == BudgetOutcome.OK


class TestMiniMaxBudgetTrackerLastDecision:
    def test_last_decision_recorded_on_check(self) -> None:
        budgets = RunBudgets(model_tokens=100)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_tokens(input_tokens=200, output_tokens=0)
        tracker.check()
        assert tracker.last_decision is not None
        assert tracker.last_decision.outcome == BudgetOutcome.BLOCKED
        assert tracker.last_decision_axis == BudgetAxis.MODEL_TOKENS

    def test_last_decision_recorded_on_enforce(self) -> None:
        budgets = RunBudgets(elapsed_seconds=10)
        tracker = MiniMaxBudgetTracker(budgets=budgets)
        tracker.record_elapsed(15.0)
        with pytest.raises(BudgetExhausted):
            tracker.enforce()
        assert tracker.last_decision_axis == BudgetAxis.ELAPSED_SECONDS


# ---------------------------------------------------------------------------
# RedGreenCycle
# ---------------------------------------------------------------------------


class TestRedGreenCycleDirectSuccess:
    """Workplan exit criterion (direct-success path):
    validation passes on the first try."""

    def test_passes_on_first_validation(self) -> None:
        runner = _FakeValidationRunner()
        # The runner returns exit_code=0 on the first call
        # when initial_validation already passes. Patch the
        # runner to start in passing mode.
        runner.run = lambda cmd: CommandResult(  # type: ignore[method-assign]
            command=cmd, exit_code=0, stdout="ok", stderr="", duration_s=0.1
        )
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("fix patch"),
        )
        assert result.passed is True
        assert result.initial_validation.passed_ is True
        assert result.final_validation.passed_ is True


class TestRedGreenCycleRemediation:
    """Workplan exit criterion (one-remediation path):
    the first validation fails, the model produces one
    remediation patch, the second validation passes."""

    def test_red_then_green(self) -> None:
        runner = _FakeValidationRunner()
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(text="broken impl"),
            remediation_response=_ok_response(text="remediation"),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("--- a/foo.py\n+++ b/foo.py\n"),
        )
        assert result.initial_validation.passed_ is False
        assert result.initial_validation.exit_code == 1
        assert result.final_validation.passed_ is True
        assert result.final_validation.exit_code == 0
        assert result.remediation_applied is True
        assert result.remediation_patch is not None

    def test_records_validation_evidence_via_callback(self) -> None:
        recorded: list[ValidationEvidence] = []

        def _record(evidence: ValidationEvidence) -> None:
            recorded.append(evidence)

        runner = _FakeValidationRunner()
        run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("patch"),
            record_evidence=_record,
        )
        assert len(recorded) == 2
        assert recorded[0].passed_ is False
        assert recorded[1].passed_ is True

    def test_no_remediation_patch_fails_closed(self) -> None:
        """If the model produces no patch, the cycle fails
        closed: final_validation equals initial_validation,
        remediation_applied is False."""

        runner = _FakeValidationRunner()
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_NoneRepairParser(),
        )
        assert result.passed is False
        assert result.remediation_applied is False
        assert result.remediation_patch is None

    def test_records_cycle_duration(self) -> None:
        runner = _FakeValidationRunner()
        clock_values = iter([100.0, 100.5])
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("patch"),
            clock=lambda: next(clock_values),
        )
        assert result.cycle_duration_s == pytest.approx(0.5)


class TestPersistCycleResult:
    def test_writes_json_under_run_dir(self, tmp_path: Path) -> None:
        runner = _FakeValidationRunner()
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("patch"),
        )
        path = persist_cycle_result(result, run_dir=tmp_path / "run")
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["passed"] is True
        assert payload["initial_validation"]["passed"] is False
        assert payload["final_validation"]["passed"] is True

    def test_creates_run_dir_if_missing(self, tmp_path: Path) -> None:
        runner = _FakeValidationRunner()
        result = run_red_green_cycle(
            task_id="task-1",
            initial_implementation_response=_ok_response(),
            remediation_response=_ok_response(),
            validation_command="pytest --no-cov -q",
            runner=runner,
            repair_patch_parser=_StaticRepairParser("patch"),
        )
        run_dir = tmp_path / "deeply" / "nested" / "run"
        path = persist_cycle_result(result, run_dir=run_dir)
        assert path == run_dir / "red-green-cycle.json"
        assert path.exists()
