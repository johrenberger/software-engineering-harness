"""RED — Slice 5 behavior 02: scenarios trace to requirements.

Per SPEC §15 ("Planning must produce bounded tasks with: ...
requirement traceability / scenario traceability") and §28
(slice 5 RED bullets):

    scenarios trace to requirements

The traceability validator must:
- compute the set of requirements referenced by tasks in a plan
- compute the set of scenarios referenced by tasks in a plan
- detect missing requirement coverage (a task with no requirement-trace)
- detect missing scenario coverage (every requirement must have at least one scenario)
- return a structured TraceabilityReport (not just bool)

Each task's requirement traces are (requirement_id → scenario_ids) pairs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.artifacts.traceability import (
    Plan,
    RequirementTrace,
    Task,
    TraceabilityReport,
    TraceabilityValidator,
    build_traceability_report,
)


def _trace(requirement_id: str, scenario_ids: tuple[str, ...] = ()) -> RequirementTrace:
    return RequirementTrace(requirement_id=requirement_id, scenario_ids=scenario_ids)


def _task(
    task_id: str = "T-1",
    *,
    requirement_traces: tuple[RequirementTrace, ...] = (_trace("FR-1", ("SCN-1",)),),
    allowed_paths: tuple[str, ...] = ("src/",),
    depends_on: tuple[str, ...] = (),
    validation_commands: tuple[str, ...] = ("pytest",),
) -> Task:
    return Task(
        task_id=task_id,
        objective="do something",
        requirement_traces=requirement_traces,
        allowed_paths=allowed_paths,
        depends_on=depends_on,
        validation_commands=validation_commands,
    )


class TestPlanStructure:
    def test_minimal_plan_round_trip(self) -> None:
        plan = Plan(plan_id="P-1", tasks=(_task(),))
        assert plan.plan_id == "P-1"
        assert len(plan.tasks) == 1

    def test_plan_rejects_empty_tasks(self) -> None:
        with pytest.raises(ValidationError):
            Plan(plan_id="P-1", tasks=())


class TestScenarioTraceability:
    def test_report_records_scenarios_per_requirement(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(_trace("FR-1", ("SCN-1", "SCN-2")),),
                ),
            ),
        )
        report = build_traceability_report(plan)
        assert "FR-1" in report.scenarios_by_requirement
        assert report.scenarios_by_requirement["FR-1"] == {"SCN-1", "SCN-2"}

    def test_report_records_requirements_per_scenario(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(
                        _trace("FR-1", ("SCN-1",)),
                        _trace("NFR-1", ("SCN-1",)),
                    ),
                ),
            ),
        )
        report = build_traceability_report(plan)
        assert report.requirements_by_scenario["SCN-1"] == {"FR-1", "NFR-1"}

    def test_report_aggregates_across_tasks(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(_trace("FR-1", ("SCN-1",)),),
                ),
                _task(
                    task_id="T-2",
                    requirement_traces=(_trace("FR-1", ("SCN-2",)),),
                ),
            ),
        )
        report = build_traceability_report(plan)
        assert report.scenarios_by_requirement["FR-1"] == {"SCN-1", "SCN-2"}

    def test_report_lists_all_referenced_requirement_ids(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(
                        _trace("FR-1", ("SCN-1",)),
                        _trace("NFR-1", ("SCN-2",)),
                    ),
                ),
                _task(
                    task_id="T-2",
                    requirement_traces=(_trace("FR-2", ("SCN-3",)),),
                ),
            ),
        )
        report = build_traceability_report(plan)
        assert report.referenced_requirements == {"FR-1", "NFR-1", "FR-2"}

    def test_report_lists_all_referenced_scenario_ids(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(_trace("FR-1", ("SCN-1",)),),
                ),
                _task(
                    task_id="T-2",
                    requirement_traces=(_trace("FR-2", ("SCN-2", "SCN-3")),),
                ),
            ),
        )
        report = build_traceability_report(plan)
        assert report.referenced_scenarios == {"SCN-1", "SCN-2", "SCN-3"}


class TestTraceabilityValidatorPasses:
    def test_validator_accepts_fully_traced_plan(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(_trace("FR-1", ("SCN-1",)),),
                ),
            ),
        )
        validator = TraceabilityValidator()
        report = validator.validate(plan)
        assert report.is_complete is True
        assert report.missing_scenarios == set()
        assert report.unmapped_requirements == set()


class TestTraceabilityValidatorFails:
    def test_validator_rejects_task_with_no_requirement_traces(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(task_id="T-1", requirement_traces=()),),
        )
        validator = TraceabilityValidator()
        report = validator.validate(plan)
        assert report.is_complete is False
        assert "T-1" in report.tasks_missing_requirements

    def test_validator_flags_requirement_without_scenario(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    task_id="T-1",
                    requirement_traces=(
                        _trace("FR-1", ("SCN-1",)),
                        _trace("FR-2", ()),  # FR-2 has no scenario
                    ),
                ),
            ),
        )
        validator = TraceabilityValidator()
        report = validator.validate(plan)
        assert report.is_complete is False
        assert report.missing_scenarios == {"FR-2"}


class TestReportShapeContract:
    def test_report_is_pydantic_model(self) -> None:
        report = TraceabilityReport(
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
        d = report.model_dump()
        assert d["is_complete"] is True

    def test_report_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            TraceabilityReport(  # type: ignore[call-arg]
                referenced_requirements=frozenset(),
                rogue_field="evil",  # type: ignore[call-arg]
            )
