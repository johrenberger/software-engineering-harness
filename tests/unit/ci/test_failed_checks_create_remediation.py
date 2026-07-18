"""Tests for SPEC §'Slice 10' RED bullet 2.

'failed checks create remediation':
- A failed required check MUST produce a remediation packet (reason +
  bounded evidence).
- Each distinct failure class maps to a stable RemediationReason.
- The packet's bounded_evidence is BoundedEvidence from slice 7
  (no raw unbounded logs).
- Multiple failed checks → multiple packets (one per failure).
"""

from __future__ import annotations

from seharness.ci.checks import (
    CheckConclusion,
    CheckRunState,
    PullRequestCheck,
    RequiredChecksView,
)
from seharness.ci.remediation import (
    CiRemediationLoop,
    RemediationReason,
    StubCiRemediationLoop,
)


def _view_with(name: str, conclusion: CheckConclusion) -> RequiredChecksView:
    return RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=(name,),
        all_checks=(
            PullRequestCheck(
                name=name,
                state=CheckRunState.COMPLETED,
                conclusion=conclusion,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )


def test_failed_required_check_creates_remediation_packet() -> None:
    view = _view_with("ci/build", CheckConclusion.FAILURE)
    loop = StubCiRemediationLoop()
    packets = loop.build_packets(view)
    assert len(packets) == 1
    p = packets[0]
    assert p.check_name == "ci/build"
    assert p.reason == RemediationReason.CHECK_FAILED


def test_timeout_conclusion_maps_to_stable_reason() -> None:
    """Mutation killer: stable enum mapping for CheckConclusion → RemediationReason."""
    view = _view_with("ci/build", CheckConclusion.TIMED_OUT)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets[0].reason == RemediationReason.CHECK_TIMEOUT


def test_cancelled_conclusion_produces_packet() -> None:
    view = _view_with("ci/build", CheckConclusion.CANCELLED)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets[0].reason == RemediationReason.CHECK_CANCELLED


def test_action_required_conclusion_produces_packet() -> None:
    view = _view_with("ci/build", CheckConclusion.ACTION_REQUIRED)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets[0].reason == RemediationReason.CHECK_ACTION_REQUIRED


def test_passed_required_check_produces_no_packet() -> None:
    view = _view_with("ci/build", CheckConclusion.SUCCESS)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets == ()


def test_skipped_conclusion_produces_no_packet() -> None:
    """Skipped checks aren't failures; no remediation needed."""
    view = _view_with("ci/build", CheckConclusion.SKIPPED)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets == ()


def test_multiple_failed_checks_yield_multiple_packets() -> None:
    view = RequiredChecksView(
        branch="ai/feature/test-slug",
        head_sha="abc123",
        required=("ci/build", "ci/lint"),
        all_checks=(
            PullRequestCheck(
                name="ci/build",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.FAILURE,
                required=True,
            ),
            PullRequestCheck(
                name="ci/lint",
                state=CheckRunState.COMPLETED,
                conclusion=CheckConclusion.FAILURE,
                required=True,
            ),
        ),
        mergeable_unknown=False,
    )
    packets = StubCiRemediationLoop().build_packets(view)
    assert len(packets) == 2
    assert {p.check_name for p in packets} == {"ci/build", "ci/lint"}


def test_pending_required_check_does_not_create_packet() -> None:
    """Pending checks are not failures; no remediation yet."""
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
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets == ()


def test_packet_has_bounded_evidence() -> None:
    """RemediationPacket MUST carry bounded_evidence (slice 7 integration)."""
    view = _view_with("ci/build", CheckConclusion.FAILURE)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets[0].bounded_evidence is not None
    # BoundedEvidence is a frozen Pydantic model (slice 7).
    bs = packets[0].bounded_evidence
    # Required surface: relevant_files, allowed_paths (per slice 7 spec)
    assert hasattr(bs, "relevant_files")
    assert hasattr(bs, "allowed_paths")


def test_packet_carries_remediation_reason_value() -> None:
    """RemediationPacket.reason is the enum, value accessed via .value."""
    view = _view_with("ci/build", CheckConclusion.FAILURE)
    packets = StubCiRemediationLoop().build_packets(view)
    assert packets[0].reason.value == "check_failed"


def test_loop_protocol_has_build_packets_only() -> None:
    """CiRemediationLoop Protocol MUST NOT expose merge/auto-merge methods.

    This is the structural guarantee against accidental auto-merge
    (SPEC §'Do not merge automatically').
    """
    members = set(dir(CiRemediationLoop))
    forbidden = ("merge", "auto_merge", "merge_pull_request")
    for m in forbidden:
        assert m not in members, f"CiRemediationLoop exposes forbidden merge method: {m}"
