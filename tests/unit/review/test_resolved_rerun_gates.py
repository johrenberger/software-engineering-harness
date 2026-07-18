"""Tests for SPEC §'Slice 8: Independent review' RED bullet 3.

'Resolved findings rerun impacted gates':
- When a finding is resolved (FIXED), the gates it touched MUST be
  re-executed before approval.
- A resolved finding with no impacted gates MUST NOT trigger any
  reruns.
- Rerun selection MUST be deterministic and traceable.
"""

from __future__ import annotations

import pytest

from seharness.review.finding import (
    Finding,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
)
from seharness.review.policy import (
    RemediationMapping,
    rerun_impacted_gates,
    resolve_finding_to_gates,
)


def _fixed_finding(
    fid: str,
    impacted_gates: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
) -> Finding:
    return Finding(
        id=fid,
        severity=FindingSeverity.HIGH,
        category=FindingCategory.CORRECTNESS,
        file=files[0] if files else "src/example.py",
        line=10,
        evidence="assertion removed",
        consequence="silent regression",
        required_action="restore assertion",
        status=FindingStatus.FIXED,
        impacted_gates=impacted_gates,
        impacted_files=files,
    )


def test_resolved_finding_with_no_impact_runs_no_gates() -> None:
    """FIXED with empty impact MUST NOT trigger reruns."""
    finding = _fixed_finding("F-1", impacted_gates=())
    gates = rerun_impacted_gates([finding])
    assert gates == ()


def test_resolved_finding_reruns_impacted_gates() -> None:
    """FIXED with impacted_gates MUST trigger exactly those gates."""
    finding = _fixed_finding("F-1", impacted_gates=("ruff-format", "pytest"))
    gates = rerun_impacted_gates([finding])
    assert set(gates) == {"ruff-format", "pytest"}


def test_multiple_resolved_findings_dedupe_gates() -> None:
    """Multiple FIXED findings MUST dedupe the rerun set."""
    f1 = _fixed_finding("F-1", impacted_gates=("ruff-format", "pytest"))
    f2 = _fixed_finding("F-2", impacted_gates=("pytest", "mypy-strict"))
    gates = rerun_impacted_gates([f1, f2])
    assert set(gates) == {"ruff-format", "pytest", "mypy-strict"}


def test_unresolved_finding_does_not_trigger_rerun() -> None:
    """OPEN findings MUST NOT trigger reruns (they're blockers)."""
    open_finding = Finding(
        id="F-1",
        severity=FindingSeverity.HIGH,
        category=FindingCategory.CORRECTNESS,
        file="src/example.py",
        line=10,
        evidence="assertion removed",
        consequence="silent regression",
        required_action="restore assertion",
        status=FindingStatus.OPEN,
        impacted_gates=("pytest",),
    )
    assert rerun_impacted_gates([open_finding]) == ()


def test_resolve_finding_to_gates_uses_files() -> None:
    """When a finding lists impacted_files but no gates, derive gates via mapping."""
    finding = _fixed_finding("F-1", files=("src/seharness/auth.py",))
    mapping = {
        "src/seharness/auth.py": ("ruff-format", "mypy-strict", "pytest"),
    }
    gates = resolve_finding_to_gates(finding, mapping)
    assert set(gates) == {"ruff-format", "mypy-strict", "pytest"}


def test_rerun_impacted_gates_returns_sorted_tuple() -> None:
    """Result MUST be deterministic (sorted tuple)."""
    finding = _fixed_finding("F-1", impacted_gates=("pytest", "ruff-format", "mypy-strict"))
    gates = rerun_impacted_gates([finding])
    assert gates == tuple(sorted(gates))


def test_remediation_mapping_is_immutable() -> None:
    """RemediationMapping MUST be a frozen mapping."""
    mapping = RemediationMapping(file_to_gates={"src/x.py": ("pytest",)})
    with pytest.raises(Exception):  # noqa: B017
        mapping.file_to_gates = {"src/y.py": ("pytest",)}  # type: ignore[misc]
