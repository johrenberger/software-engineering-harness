"""Planning phase stub (slice 5).

Per SPEC §28 Phase 4 + A1 scope: phase executors ship as stubs that
raise ``PhaseNotImplementedError``. Real plan generation lands in slice 7.

The planning phase is the consumer of ``PlanValidator`` from
``seharness.artifacts.traceability``: any real implementation must call
``PlanValidator().validate(plan)`` before yielding the plan to the
next phase.
"""

from __future__ import annotations

from typing import Any

from seharness.domain.enums import PhaseName
from seharness.phases.base import Phase, PhaseNotImplementedError


class PlanningPhase(Phase):
    """Produces a Plan (Task set) per SPEC §15 and validates it before yielding."""

    name = PhaseName.PLANNING

    def run(self, context: Any) -> Any:
        raise PhaseNotImplementedError(
            f"{type(self).__name__} (name={self.name.value!r}) not yet wired; "
            "lands in slice 7 (validation and remediation)."
        )


__all__ = ["PlanningPhase"]
