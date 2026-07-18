"""RED \u2014 Slice 7 bullet 4: exhausted retries fail the run.

Per SPEC \u00a7"Retry budgets" and slice 7 RED bullet 4, the retry budget
tracks attempts per task. When attempts are exhausted, the run fails.

Decision: (A1) per-task budget \u2014 ``RetryBudget`` is keyed by task_id
and exposes ``can_attempt`` / ``record_attempt`` / ``attempts_left``.
When ``attempts_left == 0`` and another attempt is requested, raise
``RetriesExhausted``.

The validator (slice 6 ``TaskCompletionValidator``) treats
``RetriesExhausted`` as a run-level failure that bubbles up.
"""

from __future__ import annotations

import pytest


class TestRetryBudgetBasics:
    """Basic budget operations."""

    def test_new_budget_has_full_attempts(self) -> None:
        from seharness.validation.retry import RetryBudget

        b = RetryBudget(task_id="T-1", max_attempts=3)
        assert b.attempts_left == 3
        assert b.can_attempt is True

    def test_record_attempt_decrements(self) -> None:
        from seharness.validation.retry import RetryBudget

        b = RetryBudget(task_id="T-1", max_attempts=3)
        b.record_attempt()
        assert b.attempts_left == 2
        assert b.can_attempt is True

    def test_exhausted_budget_cannot_attempt(self) -> None:
        from seharness.validation.retry import RetryBudget

        b = RetryBudget(task_id="T-1", max_attempts=2)
        b.record_attempt()
        b.record_attempt()
        assert b.attempts_left == 0
        assert b.can_attempt is False


class TestRetryBudgetExhaustion:
    """Per-task exhaustion raises ``RetriesExhausted``."""

    def test_record_attempt_when_exhausted_raises(self) -> None:
        from seharness.validation.retry import RetryBudget, RetriesExhausted

        b = RetryBudget(task_id="T-1", max_attempts=1)
        b.record_attempt()
        with pytest.raises(RetriesExhausted) as exc_info:
            b.record_attempt()
        assert "T-1" in str(exc_info.value)
        assert "1" in str(exc_info.value)  # mentions the budget

    def test_exhausted_budget_carries_task_id(self) -> None:
        from seharness.validation.retry import RetryBudget, RetriesExhausted

        b = RetryBudget(task_id="T-42", max_attempts=2)
        b.record_attempt()
        b.record_attempt()
        with pytest.raises(RetriesExhausted) as exc_info:
            b.record_attempt()
        assert exc_info.value.task_id == "T-42"
        assert exc_info.value.max_attempts == 2


class TestRetryBudgetValidation:
    """Construction validation."""

    def test_zero_max_attempts_rejected(self) -> None:
        from seharness.validation.retry import RetryBudget

        with pytest.raises(ValueError):
            RetryBudget(task_id="T-1", max_attempts=0)

    def test_negative_max_attempts_rejected(self) -> None:
        from seharness.validation.retry import RetryBudget

        with pytest.raises(ValueError):
            RetryBudget(task_id="T-1", max_attempts=-1)

    def test_empty_task_id_rejected(self) -> None:
        from seharness.validation.retry import RetryBudget

        with pytest.raises(ValueError):
            RetryBudget(task_id="", max_attempts=3)


class TestRetryBudgetRegistry:
    """``RetryBudgetRegistry`` maps task_id \u2192 budget."""

    def test_registry_returns_budget_per_task(self) -> None:
        from seharness.validation.retry import RetryBudgetRegistry

        reg = RetryBudgetRegistry(default_max_attempts=3)
        a = reg.for_task("T-1")
        b = reg.for_task("T-2")
        c = reg.for_task("T-1")
        assert a is c  # Same task returns same budget
        assert a is not b

    def test_registry_per_task_max_attempts(self) -> None:
        from seharness.validation.retry import RetryBudgetRegistry

        reg = RetryBudgetRegistry(default_max_attempts=3)
        a = reg.for_task("T-1")
        b = reg.for_task("T-2", max_attempts=5)
        assert a.max_attempts == 3
        assert b.max_attempts == 5


class TestRetriesExhaustedShape:
    """``RetriesExhausted`` is a structured exception."""

    def test_carries_task_id_and_max_attempts(self) -> None:
        from seharness.validation.retry import RetriesExhausted

        e = RetriesExhausted(task_id="T-1", max_attempts=3)
        assert e.task_id == "T-1"
        assert e.max_attempts == 3

    def test_message_includes_task_id(self) -> None:
        from seharness.validation.retry import RetriesExhausted

        e = RetriesExhausted(task_id="T-99", max_attempts=2)
        msg = str(e)
        assert "T-99" in msg