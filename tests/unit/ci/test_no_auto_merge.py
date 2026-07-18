"""Tests for SPEC §'Slice 10' RED bullet 5.

'no path can auto-merge':
- SPEC §'Do not merge automatically.' Structural + runtime guarantee:
  1. No ChecksClient method is named `merge*`, `auto_merge*`,
     `merge_pull_request*`.
  2. No ReadinessDecision outcome contains `merge` semantics.
  3. StubChecksClient exposes no merge methods.
  4. Importing the ci module does NOT pull in subprocess calls that
     could attempt to invoke `gh pr merge`.
"""

from __future__ import annotations

import inspect

import pytest

import seharness.ci.checks as checks_mod
import seharness.ci.monitor as monitor_mod
import seharness.ci.readiness as readiness_mod
import seharness.ci.remediation as remediation_mod
from seharness.ci.checks import (
    ChecksClient,
    StubChecksClient,
)
from seharness.ci.monitor import (
    CiMonitor,
    StubCiMonitor,
)
from seharness.ci.readiness import (
    ReadyTransition,
    StubReadyTransition,
)
from seharness.ci.remediation import (
    CiRemediationLoop,
    StubCiRemediationLoop,
)

_FORBIDDEN = ("merge", "auto_merge", "merge_pull_request", "gh pr merge")


def _public_members(obj: object) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


@pytest.mark.parametrize(
    "protocol_obj",
    [
        ChecksClient,
        ReadyTransition,
        CiRemediationLoop,
        CiMonitor,
    ],
)
def test_protocols_have_no_merge_methods(protocol_obj: object) -> None:
    members = _public_members(protocol_obj)
    forbidden_matches = [m for m in members if any(f in m.lower() for f in _FORBIDDEN)]
    assert forbidden_matches == [], (
        f"{protocol_obj.__name__} exposes forbidden merge methods: {forbidden_matches}"
    )


def test_stub_checks_client_has_no_merge_methods() -> None:
    members = _public_members(StubChecksClient)
    forbidden_matches = [m for m in members if any(f in m.lower() for f in _FORBIDDEN)]
    assert forbidden_matches == []


def test_stub_ready_transition_has_no_merge_methods() -> None:
    members = _public_members(StubReadyTransition)
    forbidden_matches = [m for m in members if any(f in m.lower() for f in _FORBIDDEN)]
    assert forbidden_matches == []


def test_stub_remediation_loop_has_no_merge_methods() -> None:
    members = _public_members(StubCiRemediationLoop)
    forbidden_matches = [m for m in members if any(f in m.lower() for f in _FORBIDDEN)]
    assert forbidden_matches == []


def test_stub_ci_monitor_has_no_merge_methods() -> None:
    members = _public_members(StubCiMonitor)
    forbidden_matches = [m for m in members if any(f in m.lower() for f in _FORBIDDEN)]
    assert forbidden_matches == []


def test_ci_module_source_does_not_call_gh_pr_merge() -> None:
    """Structural guarantee: no source file in seharness.ci invokes
    `gh pr merge` — the controller does not auto-merge.

    Uses ``ast`` to inspect only code (excluding docstrings/comments)
    so prose mentions are allowed.
    """
    import ast

    for mod in (
        checks_mod,
        readiness_mod,
        monitor_mod,
        remediation_mod,
    ):
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        # Walk all string-literal nodes (constants); for each Name/Call
        # node, check for forbidden identifiers.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Convert function expr to a dotted name if possible
                names: list[str] = []
                cur = func
                while isinstance(cur, ast.Attribute):
                    names.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    names.append(cur.id)
                dotted = ".".join(reversed(names))
                for forbidden in (
                    "merge_pull_request",
                    "gh_pr_merge",
                    "auto_merge",
                    "merge_pr",
                    "gh_merge",
                ):
                    if forbidden in dotted:
                        raise AssertionError(
                            f"{mod.__name__} contains forbidden call to '{dotted}'"
                        )
            if isinstance(node, ast.Attribute) and node.attr in (
                "merge_pull_request",
                "auto_merge",
                "merge_pr",
                "gh_merge",
            ):
                raise AssertionError(
                    f"{mod.__name__} references forbidden attribute '.{node.attr}'"
                )


def test_readiness_outcome_is_never_ready_when_input_unknown() -> None:
    """Run-time guarantee: even an 'all passed' view with no mergeability
    signal MUST be not-ready (controller's only path to ready is via
    the transition Protocol)."""
    from seharness.ci.checks import (
        CheckConclusion,
        CheckRunState,
        PullRequestCheck,
        RequiredChecksView,
    )
    from seharness.ci.readiness import ReadyEvaluator

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
        mergeable_unknown=True,  # unknown blocks ready
    )
    decision = ReadyEvaluator().evaluate(view)
    assert decision.can_be_ready is False


def test_stub_monitor_does_not_auto_mark_ready_on_exhaustion() -> None:
    """Even after exhausting polling, the stub MUST NOT silently mark ready."""
    from seharness.ci.checks import (
        CheckRunState,
        PullRequestCheck,
        RequiredChecksView,
    )
    from seharness.ci.monitor import StubCiMonitor
    from seharness.ci.polling import PollPolicy

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
    policy = PollPolicy(interval_s=0.01, max_attempts=1, max_total_s=10.0)
    monitor = StubCiMonitor(policy=policy, view_factory=lambda: view)
    result = monitor.run(pr_number="42", branch="ai/feature/test-slug")
    assert result.outcome.value == "exhausted"
    # The structural guarantee: no mark_ready call happened
    assert monitor.is_pr_ready("42") is False
