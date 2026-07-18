"""Finding policy dispatch table + remediation mapping.

Per SPEC §'18. Independent Review':
- Blocking findings: critical, high, policy_blocking_medium.
- A1 (declarative dispatch table) chosen — matches SPEC's fixed policy.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from seharness.review.finding import Finding, FindingSeverity


class PolicyDecision(StrEnum):
    """Closed enum. Outcome of policy evaluation."""

    APPROVE = "approve"
    BLOCK = "block"


class FindingPolicy:
    """Declarative dispatch table. Frozen at class-definition time.

    Blocking severities are fixed per SPEC §'18. Independent Review'.
    """

    BLOCKING: frozenset[FindingSeverity] = frozenset(
        {
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
            FindingSeverity.POLICY_BLOCKING_MEDIUM,
        }
    )


def apply_finding_policy(
    findings: list[Finding] | tuple[Finding, ...],
    *,
    include_reason: bool = False,
) -> PolicyDecision | tuple[PolicyDecision, str]:
    """Apply the dispatch table.

    Returns PolicyDecision.BLOCK if any finding's severity is in
    FindingPolicy.BLOCKING; otherwise PolicyDecision.APPROVE.

    If include_reason=True, returns a (decision, reason) tuple.
    """
    has_blocking = any(f.severity in FindingPolicy.BLOCKING for f in findings)
    decision = PolicyDecision.BLOCK if has_blocking else PolicyDecision.APPROVE
    if include_reason:
        reason = (
            "blocking finding → return to remediation" if has_blocking else "no blocking findings"
        )
        return (decision, reason)
    return decision


class RemediationMapping(BaseModel):
    """Maps file paths to the gates they impact.

    Per SPEC §'17. Remediation' bullet 6: 'Re-run any upstream gate
    invalidated by the change.'
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_to_gates: Mapping[str, tuple[str, ...]]

    def __getitem__(self, key: str) -> tuple[str, ...]:
        return self.file_to_gates[key]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.file_to_gates)

    def __len__(self) -> int:
        return len(self.file_to_gates)

    def __contains__(self, key: object) -> bool:
        return key in self.file_to_gates

    def items(self) -> ItemsView[str, tuple[str, ...]]:
        return self.file_to_gates.items()


def resolve_finding_to_gates(
    finding: Finding,
    mapping: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Resolve a finding's impacted_gates (and any gates derived from
    impacted_files via the mapping) to a deduplicated sorted tuple.
    """
    gates: set[str] = set(finding.impacted_gates)
    for f in finding.impacted_files:
        if f in mapping:
            gates.update(mapping[f])
    return tuple(sorted(gates))


def rerun_impacted_gates(
    findings: list[Finding] | tuple[Finding, ...],
) -> tuple[str, ...]:
    """Collect deduplicated, sorted gates from all FIXED findings.

    OPEN findings are blockers and MUST NOT trigger reruns.
    """
    gates: set[str] = set()
    for f in findings:
        if f.status.value == "fixed":
            gates.update(f.impacted_gates)
    return tuple(sorted(gates))
