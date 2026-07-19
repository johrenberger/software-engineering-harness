"""G1: targeted tests for StubChecksClient coverage.

These tests fill the coverage gap in ``src/seharness/ci/checks.py``
where ``StubChecksClient`` had uncovered error paths and constructor
behavior. Without these, checks.py sits at 79% — lifting to 88+
helps the overall fail_under target.

Coverage target: src/seharness/ci/checks.py → ≥95%.
"""

from __future__ import annotations

import pytest

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    PullRequestCheck,
    RequiredChecksView,
    StubChecksClient,
)


def _make_view(*, passed: bool = True) -> RequiredChecksView:
    """Helper: build a minimal RequiredChecksView with one check."""
    return RequiredChecksView(
        branch="main",
        head_sha="deadbeef",
        required=("ci",),
        all_checks=(
            PullRequestCheck(
                name="ci",
                state=CheckRunState.COMPLETED,
                conclusion=(CheckConclusion.SUCCESS if passed else CheckConclusion.FAILURE),
            ),
        ),
        mergeable_unknown=False,
    )


def test_stub_checks_client_records_last_pr_and_branch() -> None:
    """fetch_view must record (pr_number, branch) for last_called_with."""
    client = StubChecksClient(view_factory=_make_view)
    client.fetch_view(pr_number="42", branch="feat-x")
    assert client.last_called_with == ("42", "feat-x")


def test_stub_checks_client_no_factory_raises() -> None:
    """fetch_view without a view_factory must raise RuntimeError."""
    client = StubChecksClient()
    with pytest.raises(RuntimeError, match="view_factory"):
        client.fetch_view(pr_number="1", branch="main")


def test_stub_checks_client_last_called_with_requires_call() -> None:
    """last_called_with must assert if fetch_view was never called."""
    client = StubChecksClient(view_factory=_make_view)
    with pytest.raises(AssertionError):
        _ = client.last_called_with


def test_stub_checks_client_view_factory_asserts_instance() -> None:
    """view_factory must produce a RequiredChecksView (asserted via isinstance)."""
    # A factory returning the wrong type — internal assertion fires.
    client = StubChecksClient(view_factory=lambda: "not-a-view")
    with pytest.raises(AssertionError):
        client.fetch_view(pr_number="1", branch="main")


def test_stub_checks_client_factory_called_each_fetch() -> None:
    """fetch_view must invoke the factory each call (not cache)."""
    calls: list[int] = [0]

    def factory() -> RequiredChecksView:
        calls[0] += 1
        return _make_view()

    client = StubChecksClient(view_factory=factory)
    client.fetch_view(pr_number="1", branch="main")
    client.fetch_view(pr_number="2", branch="feat")
    assert calls[0] == 2


def test_stub_checks_client_default_view_factory_is_none() -> None:
    """Default constructor has no view_factory (forces explicit setup)."""
    client = StubChecksClient()
    # The internal attribute is None — verified indirectly via RuntimeError.
    with pytest.raises(RuntimeError):
        client.fetch_view(pr_number="1", branch="main")


def test_stub_checks_client_returns_factory_view() -> None:
    """fetch_view returns the view from the factory (not a cached one)."""
    view = _make_view(passed=True)
    client = StubChecksClient(view_factory=lambda: view)
    returned = client.fetch_view(pr_number="1", branch="main")
    assert returned is view
    assert returned.all_checks[0].name == "ci"
    assert returned.all_checks[0].is_failed is False
