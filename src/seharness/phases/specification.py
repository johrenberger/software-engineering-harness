"""Specification phase stub (slice 5).

Per SPEC §28 Phase 4 + A1 scope: phase executors ship as stubs that
raise ``PhaseNotImplementedError``. Real implementation lands in slice 6+
when the orchestrator is wired.
"""

from __future__ import annotations

from typing import Any

from seharness.domain.enums import PhaseName
from seharness.phases.base import Phase, PhaseNotImplementedError


class SpecificationPhase(Phase):
    """Produces FR / NFR / SCN artifacts per SPEC §15."""

    name = PhaseName.SPECIFICATION

    def run(self, context: Any) -> Any:
        raise PhaseNotImplementedError(
            f"{type(self).__name__} (name={self.name.value!r}) not yet wired; "
            "lands in slice 6 (task execution) / slice 7 (validation)."
        )


__all__ = ["SpecificationPhase"]
