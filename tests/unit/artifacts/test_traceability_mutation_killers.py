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
    RequirementTrace,
    Task,
    TraceabilityReport,
)


def _trace(
    requirement_id: str = "FR-1", scenario_ids: tuple[str, ...] = ("SCN-1",)
) -> RequirementTrace:
    return RequirementTrace(requirement_id=requirement_id, scenario_ids=scenario_ids)


def _task(task_id: str = "T-1") -> Task:
    return Task(
        task_id=task_id,
        objective="x",
        requirement_traces=(_trace(),),
        allowed_paths=("src/",),
        validation_commands=("pytest",),
    )


class TestPlanShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Plan(
                plan_id="P-1",
                tasks=(_task(),),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        plan = Plan(plan_id="P-1", tasks=(_task(),))
        with pytest.raises(ValidationError):
            plan.plan_id = "mutated"  # type: ignore[misc]

    def test_validates_on_assignment(self) -> None:
        plan = Plan(plan_id="P-1", tasks=(_task(),))
        with pytest.raises(ValidationError):
            plan.plan_id = 42  # type: ignore[assignment]


class TestTaskShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            Task(
                task_id="T-1",
                objective="x",
                requirement_traces=(_trace(),),
                allowed_paths=("src/",),
                validation_commands=("pytest",),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        t = _task()
        with pytest.raises(ValidationError):
            t.task_id = "mutated"  # type: ignore[misc]

    def test_depends_on_defaults_to_empty_tuple(self) -> None:
        t = _task()
        assert t.depends_on == ()

    def test_depends_on_accepts_empty_tuple(self) -> None:
        t = Task(
            task_id="T-1",
            objective="x",
            requirement_traces=(_trace(),),
            allowed_paths=("src/",),
            validation_commands=("pytest",),
            depends_on=(),
        )
        assert t.depends_on == ()

    def test_objective_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            Task(
                task_id="T-1",
                objective="",
                requirement_traces=(_trace(),),
                allowed_paths=("src/",),
                validation_commands=("pytest",),
            )

    def test_task_id_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            Task(
                task_id="",
                objective="x",
                requirement_traces=(_trace(),),
                allowed_paths=("src/",),
                validation_commands=("pytest",),
            )


class TestRequirementTraceShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            RequirementTrace(
                requirement_id="FR-1",
                scenario_ids=("SCN-1",),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        rt = _trace()
        with pytest.raises(ValidationError):
            rt.requirement_id = "mutated"  # type: ignore[misc]


class TestTraceabilityReportShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            TraceabilityReport(
                referenced_requirements=frozenset(),
                referenced_scenarios=frozenset(),
                scenarios_by_requirement={},
                requirements_by_scenario={},
                is_complete=True,
                tasks_missing_requirements=frozenset(),
                missing_scenarios=frozenset(),
                unmapped_requirements=frozenset(),
                orphan_scenarios=frozenset(),
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_complete_default_rejects_none(self) -> None:
        with pytest.raises(ValidationError):
            TraceabilityReport(  # type: ignore[call-arg]
                referenced_requirements=frozenset(),
                referenced_scenarios=frozenset(),
                scenarios_by_requirement={},
                requirements_by_scenario={},
                is_complete=None,  # type: ignore[arg-type]
            )

    def test_all_collection_fields_default_to_empty(self) -> None:
        r = TraceabilityReport(
            referenced_requirements=frozenset(),
            referenced_scenarios=frozenset(),
            scenarios_by_requirement={},
            requirements_by_scenario={},
            is_complete=True,
            tasks_missing_requirements=frozenset(),
            missing_scenarios=frozenset(),
            unmapped_requirements=frozenset(),
            orphan_scenarios=frozenset(),
        )
        assert r.referenced_requirements == frozenset()
        assert r.referenced_scenarios == frozenset()
        assert r.scenarios_by_requirement == {}
        assert r.requirements_by_scenario == {}
        assert r.tasks_missing_requirements == frozenset()
        assert r.missing_scenarios == frozenset()
        assert r.unmapped_requirements == frozenset()
        assert r.orphan_scenarios == frozenset()


class TestPlanValidationErrorShapeContract:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(TypeError):
            PlanValidationError(  # type: ignore[call-arg]
                plan_id="P-1",
                reason="missing_validation",
                task_ids=(),
                rogue_field="evil",
            )

    def test_reason_must_be_known_code(self) -> None:
        with pytest.raises(ValueError):
            PlanValidationError(
                plan_id="P-1",
                reason="unknown-reason",  # type: ignore[arg-type]
                task_ids=(),
            )

    def test_task_ids_default_to_empty_tuple(self) -> None:
        e = PlanValidationError(plan_id="P-1", reason="circular_dependency")
        assert e.task_ids == ()

    def test_plan_id_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError):
            PlanValidationError(plan_id="", reason="circular_dependency")
