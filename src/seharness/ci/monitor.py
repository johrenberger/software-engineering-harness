"""CI monitoring loop for SPEC §'Slice 10'.

Provides:
- ``PollResult`` — frozen dataclass: outcome + attempts + final state.
- ``CiMonitor`` Protocol — orchestrates polling + ready transition.
- ``StubCiMonitor`` — test impl that drives a ``view_factory`` to
  simulate CI snapshot changes between polls.

**REFACTOR bullet (SPEC §'Slice 10')**: polling policy is separated
from the GitHub transport. ``StubCiMonitor`` accepts any
``ChecksClient`` + ``ReadyEvaluator`` + ``ReadyTransition``. The
production wiring in slice 12 OpenClaw packaging injects a real
``GithubChecksClient`` + ``SubprocessBackend`` (slice 9).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .checks import ChecksClient, RequiredChecksView
from .polling import PollOutcome, PollPolicy, PollState
from .readiness import ReadyEvaluator, ReadyTransition


@dataclass(frozen=True)
class PollResult:
    """Frozen result of one ``CiMonitor.run`` invocation."""

    outcome: PollOutcome
    attempts_made: int
    elapsed_s: float
    final_state: PollState


class CiMonitor(Protocol):
    """Protocol for the CI monitoring loop.

    **Structural auto-merge prevention**: this Protocol has NO merge
    methods.
    """

    def run(self, pr_number: str, branch: str) -> PollResult: ...

    def is_pr_ready(self, pr_number: str) -> bool: ...


class StubCiMonitor:
    """In-memory ``CiMonitor`` for tests.

    Drives a ``view_factory`` to simulate CI snapshot evolution
    between polls. The factory is called once per poll attempt; the
    returned ``RequiredChecksView`` is the snapshot at that moment.

    ``stop_early`` (test-only): if set, stops after N attempts
    regardless of outcome (used to assert ``STILL_PENDING`` under
    budget without exhausting time).
    """

    def __init__(
        self,
        *,
        policy: PollPolicy | None = None,
        view_factory: Callable[[], RequiredChecksView] | None = None,
        client: ChecksClient | None = None,
        transition: ReadyTransition | None = None,
        time_source: Callable[[], float] | None = None,
    ) -> None:
        self._policy = policy or PollPolicy()
        self._view_factory = view_factory
        self._client = client
        self._transition = transition or _NoOpTransition()
        self._time_source: Callable[[], float] = (
            time_source if time_source is not None else time.monotonic
        )
        self._ready: dict[str, bool] = {}

    def run(self, pr_number: str, branch: str, *, stop_early: int | None = None) -> PollResult:
        started = self._time_source()
        attempts_made = 0

        for attempt in range(1, self._policy.max_attempts + 1):
            attempts_made = attempt
            view = self._fetch_view(pr_number, branch)

            # Check terminal conditions
            decision = ReadyEvaluator().evaluate(view)
            if decision.can_be_ready:
                # Mark ready (transition Protocol — StubReadyTransition for tests)
                if not self._transition.is_ready(pr_number):
                    self._transition.mark_ready(pr_number, view)
                self._ready[pr_number] = True
                elapsed = self._time_source() - started
                return PollResult(
                    outcome=PollOutcome.READY,
                    attempts_made=attempts_made,
                    elapsed_s=elapsed,
                    final_state=PollState(
                        attempts=attempts_made,
                        elapsed_s=elapsed,
                        started_at=str(started),
                    ),
                )

            # Stop early for tests
            if stop_early is not None and attempt >= stop_early:
                elapsed = self._time_source() - started
                return PollResult(
                    outcome=PollOutcome.STILL_PENDING,
                    attempts_made=attempts_made,
                    elapsed_s=elapsed,
                    final_state=PollState(
                        attempts=attempts_made,
                        elapsed_s=elapsed,
                        started_at=str(started),
                    ),
                )

            # Check budget
            elapsed = self._time_source() - started
            if elapsed >= self._policy.max_total_s:
                break

            # Backoff sleep (skipped for tests with interval_s < 0.1)
            if self._policy.interval_s >= 0.1:
                time.sleep(self._policy.interval_s)

        # Exhausted
        final_elapsed = self._time_source() - started
        return PollResult(
            outcome=PollOutcome.EXHAUSTED,
            attempts_made=attempts_made,
            elapsed_s=final_elapsed,
            final_state=PollState(
                attempts=attempts_made,
                elapsed_s=final_elapsed,
                started_at=str(started),
            ),
        )

    def is_pr_ready(self, pr_number: str) -> bool:
        return self._ready.get(pr_number, False)

    def _fetch_view(self, pr_number: str, branch: str) -> RequiredChecksView:
        if self._client is not None:
            return self._client.fetch_view(pr_number, branch)
        if self._view_factory is not None:
            view = self._view_factory()
            assert isinstance(view, RequiredChecksView)
            return view
        raise RuntimeError("StubCiMonitor requires either a ChecksClient or a view_factory")


class _NoOpTransition:
    """Internal: a transition that never marks ready (used when no
    transition is injected into ``StubCiMonitor``)."""

    def is_ready(self, pr_number: str) -> bool:
        return False

    def mark_ready(self, pr_number: str, view: RequiredChecksView) -> bool:
        return False
