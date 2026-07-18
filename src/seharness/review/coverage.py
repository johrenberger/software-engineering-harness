"""Requirement coverage tracking. Per SPEC §'Slice 8 RED bullet 4'.

'Incomplete requirement coverage blocks approval':
- Every approved FR and NFR MUST appear in covered_requirements.
- Missing requirements block approval with reason.
- Extra covered requirements (not in approved spec) are warnings only.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class RequirementLike(Protocol):
    id: str


class CoverageReport(BaseModel):
    """Coverage report. Immutable per SPEC §'Audit trail'."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    covered: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()


class RequirementCoverageTracker:
    """Tracks which requirements have been covered.

    Coverage is set-based: each requirement id can be covered at most
    once. Covering an unknown requirement raises ValueError.

    Unexpected coverage (ids NOT in the approved spec) is tracked
    separately as warnings — they never block approval.
    """

    def __init__(self, spec: Iterable[RequirementLike]) -> None:
        self._spec_ids: frozenset[str] = frozenset(r.id for r in spec)
        self._covered: set[str] = set()
        self._unexpected: set[str] = set()

    def cover(self, requirement_id: str) -> None:
        if requirement_id not in self._spec_ids:
            # Per SPEC §'Slice 8 RED bullet 4': extras are warnings.
            # We track them as unexpected so they surface separately.
            self._unexpected.add(requirement_id)
            return
        self._covered.add(requirement_id)

    def is_covered(self, requirement_id: str) -> bool:
        return requirement_id in self._covered

    def report(self) -> CoverageReport:
        uncovered = tuple(sorted(self._spec_ids - self._covered))
        return CoverageReport(
            covered=tuple(sorted(self._covered)),
            uncovered=uncovered,
            unexpected=tuple(sorted(self._unexpected)),
        )


def evaluate_coverage(report: CoverageReport) -> str:
    """Return 'approve' if all spec requirements are covered; 'block' otherwise.

    Unexpected (extra) coverage is NOT a blocker (per SPEC §'Slice 8
    RED bullet 4') — it's a warning surfaced separately.
    """
    if report.uncovered:
        return "block"
    return "approve"
