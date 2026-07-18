"""Tests for SPEC §'Slice 8: Independent review' RED bullet 2.

'High findings block delivery':
- Findings with severity CRITICAL, HIGH, or POLICY_BLOCKING_MEDIUM MUST
  block approval.
- LOW and INFO findings MUST NOT block approval.
- The policy MUST be enforced by a deterministic dispatch table.
"""

from __future__ import annotations

from typing import Any

import pytest

from seharness.domain.requirements import FunctionalRequirement, Scenario
from seharness.review.finding import (
    Finding,
    FindingCategory,
    FindingSeverity,
)
from seharness.review.policy import (
    FindingPolicy,
    PolicyDecision,
    apply_finding_policy,
)


def _finding(
    severity: FindingSeverity,
    category: FindingCategory = FindingCategory.CORRECTNESS,
    fid: str = "F-1",
) -> Finding:
    return Finding(
        id=fid,
        severity=severity,
        category=category,
        file="src/example.py",
        line=10,
        evidence="assertion removed",
        consequence="silent regression",
        required_action="restore assertion",
    )


def test_critical_finding_blocks() -> None:
    decision = apply_finding_policy([_finding(FindingSeverity.CRITICAL)])
    assert decision == PolicyDecision.BLOCK


def test_high_finding_blocks() -> None:
    decision = apply_finding_policy([_finding(FindingSeverity.HIGH)])
    assert decision == PolicyDecision.BLOCK


def test_policy_blocking_medium_blocks() -> None:
    decision = apply_finding_policy(
        [_finding(FindingSeverity.POLICY_BLOCKING_MEDIUM)]
    )
    assert decision == PolicyDecision.BLOCK


def test_low_finding_does_not_block() -> None:
    decision = apply_finding_policy([_finding(FindingSeverity.LOW)])
    assert decision == PolicyDecision.APPROVE


def test_info_finding_does_not_block() -> None:
    decision = apply_finding_policy([_finding(FindingSeverity.INFO)])
    assert decision == PolicyDecision.APPROVE


def test_medium_finding_does_not_block() -> None:
    """Non-policy medium is informational and MUST NOT block."""
    decision = apply_finding_policy([_finding(FindingSeverity.MEDIUM)])
    assert decision == PolicyDecision.APPROVE


def test_mixed_findings_highest_severity_wins() -> None:
    """When findings mix severities, the worst-severity decision wins."""
    findings = [
        _finding(FindingSeverity.INFO, fid="F-1"),
        _finding(FindingSeverity.LOW, fid="F-2"),
        _finding(FindingSeverity.HIGH, fid="F-3"),
    ]
    assert apply_finding_policy(findings) == PolicyDecision.BLOCK


def test_empty_findings_approve() -> None:
    assert apply_finding_policy([]) == PolicyDecision.APPROVE


def test_policy_is_dispatch_table_not_branching() -> None:
    """FindingPolicy MUST be a dispatch table (dict-like)."""
    assert isinstance(FindingPolicy.BLOCKING, (set, frozenset, dict))
    assert FindingSeverity.CRITICAL in FindingPolicy.BLOCKING
    assert FindingSeverity.HIGH in FindingPolicy.BLOCKING
    assert FindingSeverity.POLICY_BLOCKING_MEDIUM in FindingPolicy.BLOCKING


def test_policy_table_is_frozen() -> None:
    """The dispatch table MUST be immutable (no runtime mutation)."""
    assert isinstance(FindingPolicy.BLOCKING, frozenset) or (
        isinstance(FindingPolicy.BLOCKING, dict)
        and not isinstance(FindingPolicy.BLOCKING, dict)
    )


def test_blocking_finding_returns_to_remediation() -> None:
    """A BLOCK decision MUST include a reason indicating remediation route."""
    decision, reason = apply_finding_policy(
        [_finding(FindingSeverity.HIGH)], include_reason=True
    )
    assert decision == PolicyDecision.BLOCK
    assert "remediation" in reason.lower() or "fix" in reason.lower()


def test_severity_enum_is_closed() -> None:
    """FindingSeverity MUST be a closed StrEnum with exactly 6 values."""
    values = list(FindingSeverity)
    assert len(values) == 6
    name_set = {s.value for s in values}
    assert name_set == {
        "critical",
        "high",
        "policy_blocking_medium",
        "medium",
        "low",
        "info",
    }