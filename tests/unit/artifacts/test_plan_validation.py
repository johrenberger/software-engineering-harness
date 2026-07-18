"""RED — Slice 5 behavior 03: plans with missing validation fail.

Per SPEC §15 ("Reject a plan when: ... a task has no validation") and
§28 (slice 5 RED bullets):

    plans with missing validation fail

The plan validator must reject any plan that contains a task with no
``validation_commands``. The validator must also reject plans whose
tasks lack requirement traces (per SPEC §15 reject list).
"""

from __future__ import annotations

import pytest

from seharness.artifacts.traceability import (
    Plan,
    PlanValidationError,
    PlanValidator,
    RequirementTrace,
    Task,
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


class TestPlanAcceptsValid:
    def test_validator_accepts_plan_with_validation(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(validation_commands=("pytest", "ruff check")),),
        )
        PlanValidator().validate(plan)

    def test_validator_accepts_multiple_validation_commands(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    validation_commands=(
                        "ruff format --check src tests",
                        "ruff check src tests",
                        "mypy --strict src/seharness",
                        "pytest --no-cov -q",
                    ),
                ),
            ),
        )
        PlanValidator().validate(plan)


class TestPlanRejectsMissingValidation:
    def test_validator_rejects_task_with_no_validation_commands(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(validation_commands=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert "T-1" in str(excinfo.value)

    def test_validator_lists_all_tasks_missing_validation(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(task_id="T-1", validation_commands=("pytest",)),
                _task(task_id="T-2", validation_commands=()),
                _task(task_id="T-3", validation_commands=()),
            ),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        msg = str(excinfo.value)
        assert "T-2" in msg
        assert "T-3" in msg
        assert "T-1" not in msg

    def test_validator_rejects_task_with_empty_validation_command(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(validation_commands=("",)),),
        )
        with pytest.raises(PlanValidationError):
            PlanValidator().validate(plan)

    def test_validator_rejects_task_with_whitespace_only_command(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(validation_commands=("   ",)),),
        )
        with pytest.raises(PlanValidationError):
            PlanValidator().validate(plan)


class TestPlanRejectsMissingRequirements:
    def test_validator_rejects_task_with_no_requirement_traces(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(requirement_traces=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert "T-1" in str(excinfo.value)


class TestPlanValidationErrorShape:
    def test_error_includes_plan_id(self) -> None:
        plan = Plan(
            plan_id="P-fail",
            tasks=(_task(validation_commands=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert "P-fail" in str(excinfo.value)

    def test_error_includes_rejection_reason_code(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(validation_commands=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "missing_validation"
