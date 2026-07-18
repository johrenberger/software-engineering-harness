"""Tests for the Reviewer protocol + StaticReviewer test implementation.

The StaticReviewer returns findings based on declarative rules provided
at construction time. It exists to:
- give deterministic test fixtures a stable Reviewer
- allow downstream tests to assert on specific finding sets
"""

from __future__ import annotations

from typing import Any

from seharness.review.finding import (
    Finding,
    FindingCategory,
    FindingSeverity,
)
from seharness.review.reviewer import Reviewer, StaticReviewer


class _C:
    def __init__(
        self,
        covered: tuple[str, ...] = (),
        diff: str = "",
        gates: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.coverage_results = {"covered_requirements": covered}
        self.final_diff = diff
        self.validation_results = {"gates": gates}


def test_static_reviewer_returns_empty_when_no_rules() -> None:
    reviewer: Reviewer = StaticReviewer()
    assert list(reviewer.review(_C())) == []


def test_static_reviewer_returns_configured_findings() -> None:
    findings = (
        Finding(
            id="F-1",
            severity=FindingSeverity.HIGH,
            category=FindingCategory.CORRECTNESS,
            file="src/x.py",
            line=1,
            evidence="evidence",
            consequence="consequence",
            required_action="fix it",
        ),
    )
    reviewer: Reviewer = StaticReviewer(findings=findings)
    out = list(reviewer.review(_C()))
    assert out == list(findings)


def test_reviewer_returns_iterable_not_just_list() -> None:
    """Reviewer.review() MUST return an iterable."""
    reviewer: Reviewer = StaticReviewer()
    result = reviewer.review(_C())
    assert hasattr(result, "__iter__")


def test_static_reviewer_with_coverage_gap_rule() -> None:
    """StaticReviewer can be configured to emit a finding when a
    requirement is missing from coverage_results."""
    rule = Finding(
        id="F-COV-1",
        severity=FindingSeverity.HIGH,
        category=FindingCategory.COVERAGE,
        file="coverage.json",
        line=0,
        evidence="FR-1 not covered",
        consequence="unverified behaviour",
        required_action="add scenario",
    )
    reviewer: Reviewer = StaticReviewer(findings=(rule,))
    # If FR-1 is missing from coverage, the rule fires.
    out = list(reviewer.review(_C(covered=("FR-2",))))
    assert any(f.id == "F-COV-1" for f in out)
