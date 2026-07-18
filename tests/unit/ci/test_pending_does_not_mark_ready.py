"""Tests for SPEC §'Slice 10: CI monitoring' RED bullet 1.

'pending checks do not mark ready':
- While ANY required check is pending, the ready-for-review decision
  must be False, regardless of how many checks have passed.
- The decision is only made when ALL required checks are terminal.
- Required-check identification is per the ChecksClient's perspective
  (GitHub's `required` flag).
"""

from __future__ import annotations

import pytest

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    CheckStatus,
    PullRequestCheck,
    RequiredChecksView,
)
from seharness.ci.readiness import ReadyEvaluator


def test_empty_required_set_yields_not_ready() -> None:
    """No required checks → can never be ready (empty ≠ implicitly ready)."""
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=(),
        all_checks=(),
        mergeable_unknown=True,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False
    assert any("no required" in r.lower() for r in decision.blocked_by)


def test_all_required_checks_passed_yields_ready() -> None:
    """All required checks PASSED with no pending + mergeable → READY."""
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build", "ci/lint"),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.SUCCESS,
                required=True,
            ),
            PullRequestCheck(
                name="ci/lint",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.SUCCESS,
                required=True,
            ),
            PullRequestCheck(
                name="ci/docs",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.SUCCESS,
                required=False,
            ),
        ),
        mergeable_unknown=False,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is True
    assert decision.blocked_by == ()


def test_single_pending_required_check_blocks_ready() -> None:
    """If ANY required check is still pending, decision is blocked."""
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build", "ci/lint"),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.SUCCESS,
                required=True,
            ),
            PullRequestCheck(
                name="ci/lint",
                state=CheckRunState.IN_PROGRESS,
                conclusion=None,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False
    assert any("ci/lint" in r for r in decision.blocked_by)


def test_queued_state_blocks_ready() -> None:
    """A QUEUED required check is not yet terminal → blocks ready."""
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build",),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.QUEUED,
                conclusion=None,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False


def test_unknown_mergeability_blocks_ready() -> None:
    """SPEC §'Do not mark ready when' — mergeability unknown blocks."""
    view = RequiredChecksView(
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
        mergeable_unknown=True,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False
    assert any("mergeability" in r for r in decision.blocked_by)


def test_optional_passed_does_not_mask_required_pending() -> None:
    """Optional passed checks MUST NOT mask a pending required check."""
    view = RequiredChecksView(
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
            PullRequestCheck(
                name="ci/experimental",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.SUCCESS,
                required=False,
            ),
        ),
        mergeable_unknown=False,
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False
    assert any("ci/build" in r for r in decision.blocked_by)


def test_check_status_enum_values_are_stable() -> None:
    """Mutation killer: CheckStatus enum values are stable strings."""
    assert CheckStatus.PENDING.value == "pending"
    assert CheckStatus.IN_PROGRESS.value == "in_progress"
    assert CheckStatus.COMPLETED.value == "completed"
    assert CheckStatus.QUEUED.value == "queued"


def test_readiness_decision_blocked_by_is_frozen_tuple() -> None:
    """Mutation killer: blocked_by is a frozen tuple (immutable)."""
    view = RequiredChecksView(
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
    decision = ReadyEvaluator().evaluate(view)
    assert isinstance(decision.blocked_by, tuple)
    # Frozen + tuple: cannot mutate via __setattr__.
    with pytest.raises((AttributeError, TypeError)):
        decision.blocked_by = ()  # type: ignore[misc]
