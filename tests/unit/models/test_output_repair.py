"""RED tests for behavior 04: StructuredOutputRepair.

Per SPEC §10: when structured output is malformed, the adapter boundary
must attempt ONE repair and route the result. If the repair fails, the
failure is propagated as a normalized ModelError.

Per SPEC §10 (Routing): 'invalid structured output after one repair attempt'
triggers fallback.
"""

from __future__ import annotations

from typing import Any

import pytest

from seharness.domain.enums import RepairOutcome
from seharness.models import (
    ModelError,
    ModelRepair,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    StructuredOutputRepair,
)


def _ok(parsed: dict[str, Any]) -> ModelResponse:

    return ModelResponse(
        provider="minimax",
        model="minimax-M3",
        parsed=parsed,
        usage=ModelUsage(input_tokens=1, output_tokens=1),
        error=None,
        requires_repair=False,
    )


def _malformed(reason: str = "trailing_comma") -> ModelResponse:
    return ModelResponse(
        provider="minimax",
        model="minimax-M3",
        parsed=None,
        error=ModelError(kind="malformed_output", message=reason),
        requires_repair=True,
    )


def _repair_succeeded() -> ModelResponse:
    return ModelResponse(
        provider="minimax",
        model="minimax-M3",
        parsed={"repaired": True},
        error=None,
        requires_repair=False,
    )


def _repair_failed() -> ModelResponse:
    return ModelResponse(
        provider="minimax",
        model="minimax-M3",
        parsed=None,
        error=ModelError(kind="malformed_output", message="still bad"),
        requires_repair=True,
    )


class TestRepairNotNeeded:
    def test_well_formed_response_passes_through(self) -> None:
        repair = StructuredOutputRepair()
        original = _ok({"x": 1})
        result = repair.maybe_repair(original)
        assert result.outcome == RepairOutcome.NOT_NEEDED
        assert result.response is original
        assert result.attempts == 0

    def test_response_with_error_but_no_repair_flag_is_not_repaired(self) -> None:
        """If the adapter already gave up (no requires_repair flag), repair
        does NOT trigger a second attempt — the failure is final."""
        repair = StructuredOutputRepair()
        original = ModelResponse(
            provider="minimax",
            model="minimax-M3",
            parsed=None,
            error=ModelError(kind="provider_failure", message="down"),
            requires_repair=False,
        )
        result = repair.maybe_repair(original)
        assert result.outcome == RepairOutcome.REJECTED
        assert result.response is original
        assert result.attempts == 0


class TestRepairAttempts:
    def test_malformed_response_triggers_repair_attempt(self) -> None:
        """When requires_repair=True, the repair step calls the supplied
        callable exactly once and accepts the repaired response."""
        calls: list[ModelRequest] = []

        def reattempt(req: ModelRequest) -> ModelResponse:
            calls.append(req)
            return _repair_succeeded()

        repair = StructuredOutputRepair()
        result = repair.maybe_repair(_malformed(), reattempt=reattempt)
        assert result.outcome == RepairOutcome.REPAIRED
        assert result.response.parsed == {"repaired": True}
        assert result.attempts == 1
        assert len(calls) == 1

    def test_failed_repair_is_rejected_after_one_attempt(self) -> None:
        """Per SPEC: ONE repair attempt only. If the repair also fails, the
        failure is propagated — no further attempts are made."""
        calls: list[int] = []

        def reattempt(req: ModelRequest) -> ModelResponse:
            calls.append(1)
            return _repair_failed()

        repair = StructuredOutputRepair()
        result = repair.maybe_repair(_malformed(), reattempt=reattempt)
        assert result.outcome == RepairOutcome.REJECTED
        assert result.response.error is not None
        assert result.response.error.kind == "malformed_output"
        assert result.attempts == 1
        assert len(calls) == 1  # exactly one — no retries

    def test_no_reattempt_callable_means_rejected(self) -> None:
        """If the caller does not supply a reattempt callable (e.g. test-time),
        the repair step short-circuits to REJECTED — never silently succeeds."""
        repair = StructuredOutputRepair()
        result = repair.maybe_repair(_malformed(), reattempt=None)
        assert result.outcome == RepairOutcome.REJECTED
        assert result.attempts == 0


class TestRepairContext:
    def test_repair_record_records_outcome(self) -> None:

        record = ModelRepair(
            outcome=RepairOutcome.REPAIRED,
            attempts=1,
            original_error="malformed",
        )
        assert record.outcome == RepairOutcome.REPAIRED
        assert record.attempts == 1
        assert record.original_error == "malformed"

    def test_repair_record_rejects_negative_attempts(self) -> None:

        with pytest.raises(ValueError):
            ModelRepair(
                outcome=RepairOutcome.NOT_NEEDED,
                attempts=-1,
                original_error=None,
            )
