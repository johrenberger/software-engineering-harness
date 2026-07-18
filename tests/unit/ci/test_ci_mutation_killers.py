"""Pydantic config mutation killers for SPEC §'Slice 10' CI package.

Killers that mutmut 2.5.1 cannot reach (no AST RHS in Pydantic / StrEnum /
frozen dataclasses): structural configuration via construction errors.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    PullRequestCheck,
    RequiredChecksView,
)
from seharness.ci.polling import PollPolicy, PollState
from seharness.ci.readiness import ReadinessDecision
from seharness.ci.remediation import (
    RemediationPacket,
    RemediationReason,
)

# --- CheckRunState StrEnum ---


def test_check_run_state_is_str_enum() -> None:
    for state in CheckRunState:
        assert isinstance(state.value, str)
    assert repr(CheckRunState.PENDING) == "<CheckRunState.PENDING: 'pending'>"


def test_check_run_state_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        CheckRunState("__mutation_test_unknown__")


# --- CheckConclusion StrEnum ---


def test_check_conclusion_is_str_enum() -> None:
    for c in CheckConclusion:
        assert isinstance(c.value, str)


def test_check_conclusion_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        CheckConclusion("__mutation_test_unknown__")


# --- PullRequestCheck frozen BaseModel ---


def test_pull_request_check_is_frozen() -> None:
    check = PullRequestCheck(
        name="ci/build",
        state=CheckRunState.COMPLETED,
        conclusion=CheckConclusion.SUCCESS,
        required=True,
    )
    with pytest.raises(ValidationError):
        check.name = "ci/x"  # type: ignore[misc]


def test_pull_request_check_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PullRequestCheck(  # type: ignore[call-arg]
            name="ci/build",
            state=CheckRunState.COMPLETED,
            conclusion=CheckConclusion.SUCCESS,
            required=True,
            extra_kwarg="mutation",
        )


def test_pull_request_check_default_required_is_false() -> None:
    check = PullRequestCheck(
        name="ci/build",
        state=CheckRunState.COMPLETED,
        conclusion=CheckConclusion.SUCCESS,
    )
    assert check.required is False


# --- RequiredChecksView frozen BaseModel ---


def test_required_checks_view_is_frozen() -> None:
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=(),
        all_checks=(),
        mergeable_unknown=True,
    )
    with pytest.raises(ValidationError):
        view.head_sha = "mutated"


def test_required_checks_view_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        RequiredChecksView(  # type: ignore[call-arg]
            branch="ai/feature/test-slug",
            head_sha="abc123",
            required=(),
            all_checks=(),
            mergeable_unknown=True,
            bad_field="mutation",
        )


# --- PollPolicy frozen dataclass ---


def test_poll_policy_is_frozen() -> None:
    policy = PollPolicy(interval_s=30, max_attempts=10, max_total_s=1800)
    with pytest.raises((AttributeError, TypeError)):
        policy.interval_s = 60  # type: ignore[misc]


def test_poll_policy_rejects_extra_kwargs() -> None:
    with pytest.raises(TypeError):
        PollPolicy(interval_s=30, max_attempts=10, max_total_s=1800, bad=1)  # type: ignore[call-arg]


# --- PollState frozen dataclass ---


def test_poll_state_is_frozen() -> None:
    state = PollState(attempts=3, elapsed_s=120.0, started_at="2026-07-18T22:00:00Z")
    with pytest.raises((AttributeError, TypeError)):
        state.attempts = 99  # type: ignore[misc]


def test_poll_state_rejects_extra_kwargs() -> None:
    with pytest.raises(TypeError):
        PollState(  # type: ignore[call-arg]
            attempts=3, elapsed_s=120.0, started_at="x", bad=1
        )


# --- RemediationPacket frozen dataclass ---


def test_remediation_packet_is_frozen() -> None:
    from seharness.validation.remediation import BoundedEvidence

    bs = BoundedEvidence(
        failure=None, relevant_files=(), previous_green=None, allowed_paths=()
    )
    pkt = RemediationPacket(
        check_name="ci/build",
        reason=RemediationReason.CHECK_FAILED,
        bounded_evidence=bs,
    )
    with pytest.raises((AttributeError, TypeError)):
        pkt.check_name = "x"  # type: ignore[misc]


# --- ReadinessDecision frozen dataclass ---


def test_readiness_decision_is_frozen() -> None:
    decision = ReadinessDecision(can_be_ready=True, blocked_by=())
    with pytest.raises((AttributeError, TypeError)):
        decision.can_be_ready = False  # type: ignore[misc]


def test_readiness_decision_rejects_extra_kwargs() -> None:
    with pytest.raises(TypeError):
        ReadinessDecision(can_be_ready=True, blocked_by=(), bad=1)  # type: ignore[call-arg]
