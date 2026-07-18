"""Pydantic config killers for slice-8 review models.

Forces models to reject unknown fields and reject mutation of frozen
attributes — closing the loopholes mutmut exploits for `extra="allow"`
and `frozen=False` configs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.review.coverage import CoverageReport
from seharness.review.finding import (
    Finding,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
)


def _finding(**overrides: object) -> Finding:
    base: dict[str, object] = {
        "id": "F-1",
        "severity": FindingSeverity.HIGH,
        "category": FindingCategory.CORRECTNESS,
        "file": "src/x.py",
        "line": 10,
        "evidence": "evidence",
        "consequence": "consequence",
        "required_action": "fix",
    }
    base.update(overrides)
    return Finding(**base)  # type: ignore[arg-type]


def test_finding_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="F-1",
            severity=FindingSeverity.HIGH,
            category=FindingCategory.CORRECTNESS,
            file="src/x.py",
            line=10,
            evidence="e",
            consequence="c",
            required_action="r",
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_finding_rejects_invalid_severity_string() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="F-1",
            severity="BOGUS",  # type: ignore[arg-type]
            category=FindingCategory.CORRECTNESS,
            file="src/x.py",
            line=10,
            evidence="e",
            consequence="c",
            required_action="r",
        )


def test_finding_rejects_invalid_category_string() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="F-1",
            severity=FindingSeverity.HIGH,
            category="BOGUS",  # type: ignore[arg-type]
            file="src/x.py",
            line=10,
            evidence="e",
            consequence="c",
            required_action="r",
        )


def test_finding_is_frozen() -> None:
    f = _finding()
    with pytest.raises(ValidationError):
        f.severity = FindingSeverity.LOW  # type: ignore[misc]


def test_finding_rejects_negative_line() -> None:
    with pytest.raises(ValidationError):
        _finding(line=-1)


def test_finding_accepts_zero_line() -> None:
    f = _finding(line=0)
    assert f.line == 0


def test_coverage_report_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CoverageReport(
            covered=("FR-1",),
            uncovered=(),
            unexpected=(),
            extra_field="surprise",  # type: ignore[call-arg]
        )


def test_coverage_report_is_frozen() -> None:
    r = CoverageReport(covered=(), uncovered=(), unexpected=())
    with pytest.raises(ValidationError):
        r.covered = ("FR-1",)  # type: ignore[misc]


def test_finding_status_default_is_open() -> None:
    f = _finding()
    assert f.status == FindingStatus.OPEN


def test_finding_accepts_fixed_status() -> None:
    f = _finding(status=FindingStatus.FIXED)
    assert f.status == FindingStatus.FIXED


def test_finding_default_impacted_gates_empty() -> None:
    f = _finding()
    assert f.impacted_gates == ()


def test_finding_default_impacted_files_empty() -> None:
    f = _finding()
    assert f.impacted_files == ()
