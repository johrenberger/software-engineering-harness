"""Tests for SPEC §'Slice 8: Independent review' RED bullet 4.

'Incomplete requirement coverage blocks approval':
- Every approved FR and NFR MUST appear in covered_requirements.
- Missing requirements block approval with reason.
- Extra covered requirements (not in approved spec) are warnings,
  not blockers.
"""

from __future__ import annotations

from typing import Any

import pytest

from seharness.domain.requirements import (
    FunctionalRequirement,
    NonFunctionalRequirement,
)
from seharness.review.coverage import (
    CoverageReport,
    RequirementCoverageTracker,
    evaluate_coverage,
)


def _approved_spec() -> tuple[Any, ...]:
    return (
        FunctionalRequirement(
            id="FR-1",
            summary="Reset password",
            acceptance=("User submits email",),
        ),
        FunctionalRequirement(
            id="FR-2",
            summary="Lock account",
            acceptance=("5 failed attempts",),
        ),
        NonFunctionalRequirement(
            id="NFR-1",
            summary="argon2id",
            acceptance=("cost ≥ 64 MiB",),
        ),
    )


def test_full_coverage_approves() -> None:
    spec = _approved_spec()
    tracker = RequirementCoverageTracker(spec)
    for rid in ("FR-1", "FR-2", "NFR-1"):
        tracker.cover(rid)
    report = tracker.report()
    assert report.uncovered == ()
    assert evaluate_coverage(report) == "approve"


def test_missing_requirement_blocks() -> None:
    spec = _approved_spec()
    tracker = RequirementCoverageTracker(spec)
    tracker.cover("FR-1")
    tracker.cover("NFR-1")
    # FR-2 missing
    report = tracker.report()
    assert "FR-2" in report.uncovered
    assert evaluate_coverage(report) == "block"


def test_extra_covered_requirement_does_not_block() -> None:
    """Extra covered reqs (not in approved spec) are warnings only."""
    spec = _approved_spec()
    tracker = RequirementCoverageTracker(spec)
    for rid in ("FR-1", "FR-2", "NFR-1", "FR-999"):
        tracker.cover(rid)
    report = tracker.report()
    assert report.uncovered == ()
    assert report.unexpected == ("FR-999",)
    assert evaluate_coverage(report) == "approve"


def test_zero_coverage_blocks() -> None:
    spec = _approved_spec()
    tracker = RequirementCoverageTracker(spec)
    report = tracker.report()
    assert set(report.uncovered) == {"FR-1", "FR-2", "NFR-1"}
    assert evaluate_coverage(report) == "block"


def test_empty_spec_approves() -> None:
    """A spec with no requirements trivially passes coverage."""
    tracker = RequirementCoverageTracker(())
    report = tracker.report()
    assert report.uncovered == ()
    assert evaluate_coverage(report) == "approve"


def test_coverage_report_is_immutable() -> None:
    report = CoverageReport(
        covered=("FR-1",), uncovered=("FR-2",), unexpected=()
    )
    with pytest.raises(Exception):  # noqa: B017
        report.covered = ()  # type: ignore[misc]


def test_tracker_rejects_unknown_requirement() -> None:
    """Covering an unknown requirement raises an error."""
    tracker = RequirementCoverageTracker(_approved_spec())
    with pytest.raises(ValueError, match="unknown"):
        tracker.cover("FR-999")


def test_tracker_idempotent_cover() -> None:
    """Covering the same requirement twice is allowed."""
    tracker = RequirementCoverageTracker(_approved_spec())
    tracker.cover("FR-1")
    tracker.cover("FR-1")
    report = tracker.report()
    assert "FR-1" not in report.uncovered