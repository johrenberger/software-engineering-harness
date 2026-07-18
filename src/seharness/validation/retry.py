"""Retry budgets for slice 7.

Per SPEC \u00a7"Retry budgets" and slice 7 RED bullet 4, the budget
tracks attempts per task. When attempts are exhausted, the run
fails. Decision: (A1) per-task budget.

``RetryBudget`` is the per-task primitive: ``can_attempt`` /
``record_attempt`` / ``attempts_left``. When ``record_attempt`` is
called and ``attempts_left == 0``, raises ``RetriesExhausted``.

``RetryBudgetRegistry`` is the multi-task wrapper: ``for_task(task_id)``
returns (or creates) the budget for that task. Per-task override via
``for_task(task_id, max_attempts=...)``.
"""

from __future__ import annotations


class RetriesExhausted(RuntimeError):
    """Raised when a task's retry budget is exhausted.

    Carries ``task_id`` and ``max_attempts`` so the orchestrator can
    route the failure appropriately.
    """

    def __init__(self, task_id: str, max_attempts: int) -> None:
        super().__init__(f"task {task_id!r} exhausted retry budget (max_attempts={max_attempts})")
        self.task_id = task_id
        self.max_attempts = max_attempts


class RetryBudget:
    """Per-task attempt counter.

    Construction validates ``max_attempts > 0`` and a non-empty
    ``task_id``. ``record_attempt`` is the only state mutation; it
    raises ``RetriesExhausted`` when called after the budget is gone.
    """

    def __init__(self, *, task_id: str, max_attempts: int) -> None:
        if not task_id:
            raise ValueError("task_id must be a non-empty string")
        if max_attempts <= 0:
            raise ValueError(f"max_attempts must be > 0, got {max_attempts}")
        self.task_id = task_id
        self.max_attempts = max_attempts
        self._attempts_made = 0

    @property
    def attempts_left(self) -> int:
        return self.max_attempts - self._attempts_made

    @property
    def can_attempt(self) -> bool:
        return self.attempts_left > 0

    @property
    def attempts_made(self) -> int:
        return self._attempts_made

    def record_attempt(self) -> None:
        """Record an attempt; raises if budget exhausted."""
        if not self.can_attempt:
            raise RetriesExhausted(self.task_id, self.max_attempts)
        self._attempts_made += 1


class RetryBudgetRegistry:
    """Map ``task_id`` \u2192 ``RetryBudget`` with a default ceiling.

    Per-task overrides via ``for_task(task_id, max_attempts=...)``.
    """

    def __init__(self, *, default_max_attempts: int) -> None:
        if default_max_attempts <= 0:
            raise ValueError(f"default_max_attempts must be > 0, got {default_max_attempts}")
        self._default = default_max_attempts
        self._budgets: dict[str, RetryBudget] = {}

    def for_task(self, task_id: str, *, max_attempts: int | None = None) -> RetryBudget:
        """Return the budget for ``task_id`` (creating it if needed)."""
        existing = self._budgets.get(task_id)
        if existing is not None:
            return existing
        budget = RetryBudget(
            task_id=task_id,
            max_attempts=max_attempts if max_attempts is not None else self._default,
        )
        self._budgets[task_id] = budget
        return budget


__all__ = [
    "RetriesExhausted",
    "RetryBudget",
    "RetryBudgetRegistry",
]
