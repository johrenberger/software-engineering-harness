"""Impact phase stub (slice 5).

Per SPEC §28 Phase 4 + A1 scope: phase executors ship as stubs that
raise ``PhaseNotImplementedError``. Real implementation lands in slice 6+.
"""

from __future__ import annotations

from typing import Any

from seharness.domain.enums import PhaseName
from seharness.phases.base import Phase, PhaseNotImplementedError


class ImpactPhase(Phase):
    """Produces impact analysis artifacts per SPEC §15 (impact analysis section)."""

    name = PhaseName.IMPACT

    def run(self, context: Any) -> Any:
        raise PhaseNotImplementedError(
            f"{type(self).__name__} (name={self.name.value!r}) not yet wired; "
            "lands in slice 6 (task execution) / slice 7 (validation)."
        )


__all__ = ["ImpactPhase"]
