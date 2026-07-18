"""Readiness evaluation + transition for SPEC §'Slice 10' CI monitoring.

Provides:
- ``ReadinessDecision`` — frozen dataclass with can_be_ready + blocked_by.
- ``ReadyEvaluator`` — deterministic evaluation of a
  ``RequiredChecksView``.
- ``ReadyTransition`` Protocol + ``StubReadyTransition``.

**SPEC §'Do not mark ready when'** (any of):
- any required check is pending
- any required check failed
- mergeability is unknown
- (slice 8) a new review-blocking defect appears (out-of-scope here,
  the reviewer policy decides)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .checks import RequiredChecksView


@dataclass(frozen=True)
class ReadinessDecision:
    """Frozen dataclass capturing the readiness decision + reasons.

    ``blocked_by`` is a tuple of human-readable reasons — if any are
    present, ``can_be_ready`` is False.
    """

    can_be_ready: bool
    blocked_by: tuple[str, ...]


class ReadyEvaluator:
    """Deterministic evaluator of a ``RequiredChecksView``.

    No subprocess calls. No I/O. Pure function of the snapshot.
    """

    def evaluate(self, view: RequiredChecksView) -> ReadinessDecision:
        reasons: list[str] = []

        if not view.required:
            reasons.append("no required checks reported")

        for name in view.required:
            check = next((c for c in view.all_checks if c.name == name), None)
            if check is None:
                reasons.append(f"required check missing: {name}")
                continue
            if not check.is_terminal:
                reasons.append(f"required check not terminal: {name} (state={check.state.value})")
                continue
            # is_terminal implies conclusion is not None
            assert check.conclusion is not None
            if check.is_failed:
                reasons.append(
                    f"required check failed: {name} (conclusion={check.conclusion.value})"
                )

        if view.mergeable_unknown:
            reasons.append("mergeability unknown")

        return ReadinessDecision(
            can_be_ready=len(reasons) == 0,
            blocked_by=tuple(reasons),
        )


class ReadyTransition(Protocol):
    """Protocol for marking a draft PR ready-for-review.

    **Structural auto-merge prevention**: this Protocol deliberately
    does NOT declare a ``merge`` / ``merge_pull_request`` method.
    """

    def mark_ready(self, pr_number: str, view: RequiredChecksView) -> bool: ...

    def is_ready(self, pr_number: str) -> bool: ...


class StubReadyTransition:
    """In-memory ``ReadyTransition`` for tests.

    Holds an in-memory set of "ready" PR numbers. Refuses to mark
    ready if ``ReadyEvaluator`` says ``can_be_ready`` is False.
    """

    def __init__(self) -> None:
        self._ready: dict[str, bool] = {}
        self._evaluator = ReadyEvaluator()

    def mark_ready(self, pr_number: str, view: RequiredChecksView) -> bool:
        decision = self._evaluator.evaluate(view)
        if not decision.can_be_ready:
            raise RuntimeError(f"PR {pr_number} not ready: {'; '.join(decision.blocked_by)}")
        self._ready[pr_number] = True
        return True

    def is_ready(self, pr_number: str) -> bool:
        return self._ready.get(pr_number, False)
