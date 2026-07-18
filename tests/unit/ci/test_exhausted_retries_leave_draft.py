"""Tests for SPEC §'Slice 10' RED bullet 4.

'exhausted CI retries leave the PR draft':
- PollPolicy enforces a hard ceiling on attempts and total time.
- CiMonitor returns a PollResult indicating the budget was exhausted;
  the PR MUST remain a draft (not transitioned to ready).
- Polling respects exponential backoff (interval_s * 2^attempts).
"""

from __future__ import annotations

import pytest

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    PullRequestCheck,
    RequiredChecksView,
)
from seharness.ci.monitor import CiMonitor, PollResult, StubCiMonitor
from seharness.ci.polling import PollOutcome, PollPolicy, PollState


def _failing_view() -> RequiredChecksView:
    return RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build",),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.FAILURE,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )


def _pending_view() -> RequiredChecksView:
    return RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build",),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.IN_PROGRESS,
                conclusion=None,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )


def test_poll_policy_is_frozen() -> None:
    """Mutation killer: PollPolicy frozen + extra=forbid."""
    policy = PollPolicy(interval_s=30, max_attempts=10, max_total_s=1800)
    with pytest.raises((AttributeError, TypeError)):
        policy.interval_s = 60  # type: ignore[misc]
    with pytest.raises(TypeError):
        PollPolicy(interval_s=30, max_attempts=10, bad_kw="x")  # type: ignore[call-arg]


def test_poll_policy_rejects_invalid_intervals() -> None:
    """Mutation killer: validation rejects interval_s <= 0."""
    with pytest.raises(ValueError):
        PollPolicy(interval_s=0, max_attempts=10, max_total_s=1800)
    with pytest.raises(ValueError):
        PollPolicy(interval_s=-5, max_attempts=10, max_total_s=1800)


def test_poll_policy_rejects_invalid_max_attempts() -> None:
    with pytest.raises(ValueError):
        PollPolicy(interval_s=30, max_attempts=0, max_total_s=1800)


def test_poll_policy_rejects_invalid_max_total() -> None:
    with pytest.raises(ValueError):
        PollPolicy(interval_s=30, max_attempts=10, max_total_s=0)


def test_poll_policy_defaults() -> None:
    """Default values: 30s interval, 20 attempts, 1800s total."""
    policy = PollPolicy()
    assert policy.interval_s == 30
    assert policy.max_attempts == 20
    assert policy.max_total_s == 1800


def test_poll_outcome_enum_values_are_stable() -> None:
    """Mutation killer: stable string values."""
    assert PollOutcome.READY.value == "ready"
    assert PollOutcome.EXHAUSTED.value == "exhausted"
    assert PollOutcome.STILL_PENDING.value == "still_pending"


def test_poll_state_tracks_attempts() -> None:
    """PollState is a frozen dataclass tracking attempts + elapsed."""
    state = PollState(attempts=3, elapsed_s=120.0, started_at="2026-07-18T22:00:00Z")
    assert state.attempts == 3
    assert state.elapsed_s == 120.0
    with pytest.raises((AttributeError, TypeError)):
        state.attempts = 99  # type: ignore[misc]


def test_exhausted_budget_returns_exhausted_outcome() -> None:
    """PollPolicy with max_attempts=2, then a pending check on attempt 2
    → STILL_PENDING on attempt 1, EXHAUSTED on attempt 2."""
    policy = PollPolicy(interval_s=0.01, max_attempts=2, max_total_s=10.0)
    monitor = StubCiMonitor(policy=policy, view_factory=lambda: _pending_view())  # noqa: PLW0108
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug")
    assert isinstance(result, PollResult)
    assert result.outcome == PollOutcome.EXHAUSTED
    assert result.attempts_made == 2


def test_exhausted_total_time_returns_exhausted_outcome() -> None:
    """max_total_s=0.01 forces exhaustion regardless of attempts."""
    policy = PollPolicy(interval_s=0.01, max_attempts=100, max_total_s=0.01)
    monitor = StubCiMonitor(policy=policy, view_factory=lambda: _pending_view())  # noqa: PLW0108
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug")
    assert result.outcome == PollOutcome.EXHAUSTED


def test_exhausted_result_leaves_pr_as_draft() -> None:
    """After exhaustion, is_ready must remain False (no transition)."""
    policy = PollPolicy(interval_s=0.01, max_attempts=1, max_total_s=10.0)
    monitor = StubCiMonitor(policy=policy, view_factory=lambda: _pending_view())  # noqa: PLW0108
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug")
    assert result.outcome == PollOutcome.EXHAUSTED
    # StubCiMonitor MUST NOT auto-mark ready
    assert monitor.is_pr_ready("42") is False


def test_poll_returns_ready_when_all_green_first_attempt() -> None:
    def _green() -> RequiredChecksView:
        return RequiredChecksView(
            branch="ai/feature/test-slug",
            head_sha="abc123",
            required=("ci/build",),
            all_checks=(
                PullRequestCheck(
                    name="ci/build",
                    state=CheckRunState.COMPLETED,
                    conclusion=CheckConclusion.SUCCESS,
                    required=True,
                ),
            ),
            mergeable_unknown=False,
        )

    policy = PollPolicy(interval_s=0.01, max_attempts=5, max_total_s=10.0)
    monitor = StubCiMonitor(policy=policy, view_factory=_green)
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug")
    assert result.outcome == PollOutcome.READY


def test_poll_returns_still_pending_under_budget() -> None:
    """If check is pending and budget remains, outcome = STILL_PENDING."""
    policy = PollPolicy(interval_s=0.01, max_attempts=10, max_total_s=10.0)
    monitor = StubCiMonitor(policy=policy, view_factory=lambda: _pending_view())  # noqa: PLW0108
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug", stop_early=3)
    assert result.outcome == PollOutcome.STILL_PENDING
    assert result.attempts_made == 3


def test_ci_monitor_protocol_has_no_merge() -> None:
    """CiMonitor Protocol MUST NOT expose merge/auto-merge."""
    members = set(dir(CiMonitor))
    forbidden = ("merge", "auto_merge", "merge_pull_request")
    for m in forbidden:
        assert m not in members, f"CiMonitor exposes forbidden merge method: {m}"
