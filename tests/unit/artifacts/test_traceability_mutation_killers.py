"""Mutation killers for the traceability / plan domain models (slice 5).

Per SPEC §"Mandatory Mutation Testing" the new logical units introduced in
slice 5 (Plan, Task, PlanValidator, TraceabilityValidator) must have:
- extra="forbid" (no rogue fields leak into persisted artifacts)
- frozen=True (artifacts are immutable once written)
- validate_assignment=True (mid-run mutations are rejected)

These tests exercise those invariants directly so mutmut's argument /
keyword / assignment mutations on the Pydantic ConfigDict are caught.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.artifacts.traceability import (
    Plan,
    PlanValidationError,
    Task,
    TraceabilityReport,
)


class TestPlanShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Plan(
                plan_id="P-1",
                tasks=(
                    Task(
                        task_id="T-1",
                        objective="x",
                        requirement_ids=("FR-1",),
                        scenario_ids=("SCN-1",),
                        allowed_paths=("src/",),
                        validation_commands=("pytest",),
                    ),
                ),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                Task(
                    task_id="T-1",
                    objective="x",
                    requirement_ids=("FR-1",),
                    scenario_ids=("SCN-1",),
                    allowed_paths=("src/",),
                    validation_commands=("pytest",),
                ),
            ),
        )
        with pytest.raises(ValidationError):
            plan.plan_id = "mutated"  # type: ignore[misc]

    def test_validates_on_assignment(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                Task(
                    task_id="T-1",
                    objective="x",
                    requirement_ids=("FR-1",),
                    scenario_ids=("SCN-1",),
                    allowed_paths=("src/",),
                    validation_commands=("pytest",),
                ),
            ),
        )
        with pytest.raises(ValidationError):
            plan.plan_id = 42  # type: ignore[assignment]


class TestTaskShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Task(
                task_id="T-1",
                objective="x",
                requirement_ids=("FR-1",),
                scenario_ids=("SCN-1",),
                allowed_paths=("src/",),
                validation_commands=("pytest",),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        t = Task(
            task_id="T-1",
            objective="x",
            requirement_ids=("FR-1",),
            scenario_ids=("SCN-1",),
            allowed_paths=("src/",),
            validation_commands=("pytest",),
        )
        with pytest.raises(ValidationError):
            t.task_id = "mutated"  # type: ignore[misc]

    def test_depends_on_defaults_to_empty_tuple(self) -> None:
        """Mutation depends_on default must remain empty tuple."""
        t = Task(
            task_id="T-1",
            objective="x",
            requirement_ids=("FR-1",),
            scenario_ids=("SCN-1",),
            allowed_paths=("src/",),
            validation_commands=("pytest",),
        )
        assert t.depends_on == ()

    def test_depends_on_accepts_empty_tuple(self) -> None:
        t = Task(
            task_id="T-1",
            objective="x",
            requirement_ids=("FR-1",),
            scenario_ids=("SCN-1",),
            allowed_paths=("src/",),
            validation_commands=("pytest",),
            depends_on=(),
        )
        assert t.depends_on == ()

    def test_objective_rejects_empty_string(self) -> None:
        """Mutation objective min_length=1 -> 0 must be killed."""
        with pytest.raises(ValidationError):
            Task(
                task_id="T-1",
                objective="",
                requirement_ids=("FR-1",),
                scenario_ids=("SCN-1",),
                allowed_paths=("src/",),
                validation_commands=("pytest",),
            )


class TestTraceabilityReportShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            TraceabilityReport(
                referenced_requirements=set(),
                referenced_scenarios=set(),
                scenarios_by_requirement={},
                requirements_by_scenario={},
                is_complete=True,
                tasks_missing_requirements=set(),
                missing_scenarios=set(),
                unmapped_requirements=set(),
                orphan_scenarios=set(),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_complete_default_rejects_none(self) -> None:
        """is_complete is required, not optional — must not be None."""
        with pytest.raises(ValidationError):
            TraceabilityReport(  # type: ignore[call-arg]
                referenced_requirements=set(),
                referenced_scenarios=set(),
                scenarios_by_requirement={},
                requirements_by_scenario={},
                is_complete=None,  # type: ignore[arg-type]
            )

    def test_all_collection_fields_default_to_empty(self) -> None:
        """Mutation of default_factory to a different empty value must be killed."""
        r = TraceabilityReport(
            referenced_requirements=set(),
            referenced_scenarios=set(),
            scenarios_by_requirement={},
            requirements_by_scenario={},
            is_complete=True,
            tasks_missing_requirements=set(),
            missing_scenarios=set(),
            unmapped_requirements=set(),
            orphan_scenarios=set(),
        )
        assert r.referenced_requirements == set()
        assert r.referenced_scenarios == set()
        assert r.scenarios_by_requirement == {}
        assert r.requirements_by_scenario == {}
        assert r.tasks_missing_requirements == set()
        assert r.missing_scenarios == set()
        assert r.unmapped_requirements == set()
        assert r.orphan_scenarios == set()


class TestPlanValidationErrorShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            PlanValidationError(  # type: ignore[call-arg]
                plan_id="P-1",
                reason="missing_validation",
                task_ids=(),
                rogue_field="evil",
            )

    def test_reason_must_be_known_code(self) -> None:
        """Mutation that drops the Literal on reason must be killed."""
        with pytest.raises(ValidationError):
            PlanValidationError(
                plan_id="P-1",
                reason="unknown-reason",  # type: ignore[arg-type]
                task_ids=(),
            )

    def test_task_ids_default_to_empty_tuple(self) -> None:
        e = PlanValidationError(plan_id="P-1", reason="circular_dependency")
        assert e.task_ids == ()

    def test_plan_id_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            PlanValidationError(plan_id="", reason="circular_dependency")