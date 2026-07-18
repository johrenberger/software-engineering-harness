"""RED — Slice 5 phase boundaries (Specification / Impact / Planning).

Per SPEC §28 Phase 4 ("Workflow phases") and A1 scope decision: the
slice-5 phase classes ship as stub executors whose ``run()`` raises
``NotImplementedError("not yet wired; slice 6/7")``. Real plan
generation lands later.

This file covers the phase ABC contract:
- ``Phase`` is abstract — cannot be instantiated directly
- concrete subclasses must implement ``run()``
- the ABC exposes ``name`` and ``phase_kind`` class attributes
- the three slice-5 phases register under their canonical
  ``PhaseName.SPECIFICATION`` / ``IMPACT`` / ``PLANNING`` values
"""

from __future__ import annotations

import pytest

import seharness.phases
from seharness.domain.enums import PhaseName
from seharness.phases.base import Phase, PhaseNotImplementedError
from seharness.phases.impact import ImpactPhase
from seharness.phases.planning import PlanningPhase
from seharness.phases.specification import SpecificationPhase


class TestPhaseIsAbstract:
    def test_cannot_instantiate_abc_directly(self) -> None:
        with pytest.raises(TypeError):
            Phase()  # type: ignore[abstract]

    def test_subclass_without_run_is_abstract(self) -> None:
        class _IncompletePhase(Phase):
            name = PhaseName.SPECIFICATION

        with pytest.raises(TypeError):
            _IncompletePhase()  # type: ignore[abstract]

    def test_subclass_with_run_is_instantiable(self) -> None:
        class _CompletePhase(Phase):
            name = PhaseName.SPECIFICATION

            def run(self, context):  # type: ignore[override]
                return None

        phase = _CompletePhase()
        assert phase.name == PhaseName.SPECIFICATION


class TestPhaseNotImplementedError:
    def test_subclasses_stub_run_raises_not_implemented(self) -> None:
        """A1 scope: phase classes ship as stub executors (slice 6/7 wires them)."""
        for cls in (SpecificationPhase, ImpactPhase, PlanningPhase):
            phase = cls()
            with pytest.raises(PhaseNotImplementedError) as excinfo:
                phase.run(None)  # type: ignore[arg-type]
            assert cls.__name__ in str(excinfo.value) or phase.name.value in str(excinfo.value)

    def test_phase_not_implemented_error_is_an_exception(self) -> None:
        assert issubclass(PhaseNotImplementedError, Exception)


class TestPhaseNameRegistration:
    def test_specification_phase_uses_phase_name_specification(self) -> None:
        assert SpecificationPhase().name == PhaseName.SPECIFICATION

    def test_impact_phase_uses_phase_name_impact(self) -> None:
        assert ImpactPhase().name == PhaseName.IMPACT

    def test_planning_phase_uses_phase_name_planning(self) -> None:
        assert PlanningPhase().name == PhaseName.PLANNING


class TestPhaseRegistry:
    """The ``phases/__init__.py`` package must export the three concrete classes."""

    def test_package_exports_specification_phase(self) -> None:

        assert hasattr(seharness.phases, "SpecificationPhase")

    def test_package_exports_impact_phase(self) -> None:

        assert hasattr(seharness.phases, "ImpactPhase")

    def test_package_exports_planning_phase(self) -> None:

        assert hasattr(seharness.phases, "PlanningPhase")

    def test_package_exports_phase_abc(self) -> None:

        assert hasattr(seharness.phases, "Phase")

    def test_package_exports_phase_not_implemented_error(self) -> None:

        assert hasattr(seharness.phases, "PhaseNotImplementedError")
