"""Phase executor base class (slice 5).

Per SPEC §28 Phase 4 ("Workflow phases") and A1 scope decision: the
slice-5 phase classes ship as stub executors whose ``run()`` raises
``PhaseNotImplementedError``. The concrete ``SpecificationPhase``,
``ImpactPhase``, and ``PlanningPhase`` subclasses live in their own
modules so the orchestrator (slice 9) can wire them independently of
the data model and validators added in this slice.

This module intentionally holds no business logic — it is the
abstract boundary that downstream slices depend on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from seharness.domain.enums import PhaseName


class PhaseNotImplementedError(NotImplementedError):
    """Raised when a phase executor has not yet been wired (slice 6/7 work)."""


class Phase(ABC):
    """Abstract base class for workflow phase executors.

    Each phase carries a ``name`` (one of ``PhaseName``) and a single
    ``run(context)`` entry point that downstream orchestrators invoke
    during a run. Slice 5 ships the boundary only; concrete executors
    raise ``PhaseNotImplementedError`` until slices 6+ implement them.
    """

    #: PhaseName discriminator — must be set by every subclass.
    name: PhaseName

    @abstractmethod
    def run(self, context: Any) -> Any:
        """Execute the phase. Returns a phase-specific result."""
        raise PhaseNotImplementedError(
            f"phase {self.name.value!r} not yet wired; implementation lands in slice 6+"
        )


__all__ = [
    "Phase",
    "PhaseNotImplementedError",
]
