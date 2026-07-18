"""Tests for SPEC §'Slice 10' RED bullet 3.

'green required checks mark the draft PR ready':
- When all required checks are PASSED AND mergeable AND no review-blocking
  findings → ReadyTransition.mark_ready() succeeds and returns True.
- Already-ready transition is idempotent (returns True).
- mergeable_unknown=True blocks the transition (already covered in
  test_pending_does_not_mark_ready.py; here we exercise the Protocol).
"""

from __future__ import annotations

import pytest

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    PullRequestCheck,
    RequiredChecksView,
)
from seharness.ci.readiness import (
    ReadyTransition,
    StubReadyTransition,
)


def test_mark_ready_transition_succeeds_when_all_green() -> None:
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
        ),
        mergeable_unknown=False,
    )
    transition = StubReadyTransition()
    assert transition.mark_ready("42", view) is True
    assert transition.is_ready("42") is True


def test_mark_ready_idempotent() -> None:
    """Calling mark_ready twice on the same PR returns True both times."""
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
        mergeable_unknown=False,
    )
    transition = StubReadyTransition()
    assert transition.mark_ready("42", view) is True
    assert transition.mark_ready("42", view) is True
    assert transition.is_ready("42") is True


def test_is_ready_false_for_unknown_pr() -> None:
    transition = StubReadyTransition()
    assert transition.is_ready("99") is False


def test_mark_ready_refused_on_pending_check() -> None:
    """Protocol refusal: pending required check blocks mark_ready."""
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
    transition = StubReadyTransition()
    with pytest.raises(NotReadyError := RuntimeError):  # noqa: F841
        transition.mark_ready("42", view)


def test_mark_ready_refused_on_failed_check() -> None:
    view = RequiredChecksView(
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
    transition = StubReadyTransition()
    with pytest.raises(RuntimeError):
        transition.mark_ready("42", view)


def test_mark_ready_refused_on_unknown_mergeability() -> None:
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
    transition = StubReadyTransition()
    with pytest.raises(RuntimeError):
        transition.mark_ready("42", view)


def test_ready_transition_protocol_has_no_merge() -> None:
    """ReadyTransition Protocol MUST NOT expose merge/auto-merge."""
    members = set(dir(ReadyTransition))
    forbidden = ("merge", "auto_merge", "merge_pull_request")
    for m in forbidden:
        assert m not in members, (
            f"ReadyTransition exposes forbidden merge method: {m}"
        )


def test_stub_ready_tracks_multiple_prs_independently() -> None:
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
        mergeable_unknown=False,
    )
    transition = StubReadyTransition()
    transition.mark_ready("42", view)
    assert transition.is_ready("42") is True
    assert transition.is_ready("99") is False