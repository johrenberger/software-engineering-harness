"""Phase executor package (slice 5).

Per SPEC §28 Phase 4: Specification, Impact, Planning phase boundaries.
Slice 5 ships the abstract base + three concrete stubs. Real plan
generation lands in slice 7; orchestrator wiring in slice 9.
"""

from __future__ import annotations

from seharness.phases.base import Phase, PhaseNotImplementedError
from seharness.phases.impact import ImpactPhase
from seharness.phases.planning import PlanningPhase
from seharness.phases.specification import SpecificationPhase

__all__ = [
    "ImpactPhase",
    "Phase",
    "PhaseNotImplementedError",
    "PlanningPhase",
    "SpecificationPhase",
]
