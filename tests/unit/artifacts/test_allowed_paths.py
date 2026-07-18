"""RED — Slice 5 behavior 05: empty allowed paths fail.

Per SPEC §15 ("Reject a plan when: ... allowed paths are empty") and
§28 (slice 5 RED bullets):

    empty allowed paths fail

The validator must reject any task whose ``allowed_paths`` tuple is empty
or contains only whitespace / empty-string entries. Tasks must declare
where they are permitted to write so the task execution service (slice 6)
can enforce authorization.
"""

from __future__ import annotations

import pytest

from seharness.artifacts.traceability import (
    Plan,
    PlanValidationError,
    PlanValidator,
    Task,
)


def _task(
    task_id: str = "T-1",
    *,
    allowed_paths: tuple[str, ...] = ("src/",),
    requirement_ids: tuple[str, ...] = ("FR-1",),
    scenario_ids: tuple[str, ...] = ("SCN-1",),
    depends_on: tuple[str, ...] = (),
    validation_commands: tuple[str, ...] = ("pytest",),
) -> Task:
    return Task(
        task_id=task_id,
        objective="do something",
        requirement_ids=requirement_ids,
        scenario_ids=scenario_ids,
        allowed_paths=allowed_paths,
        depends_on=depends_on,
        validation_commands=validation_commands,
    )


class TestAcceptsNonEmptyAllowedPaths:
    def test_single_path(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=("src/",)),),
        )
        PlanValidator().validate(plan)

    def test_multiple_paths(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(
                    allowed_paths=(
                        "src/seharness/models/",
                        "tests/unit/models/",
                    ),
                ),
            ),
        )
        PlanValidator().validate(plan)

    def test_absolute_path(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=("/abs/path",)),),
        )
        PlanValidator().validate(plan)


class TestRejectsEmptyAllowedPaths:
    def test_empty_tuple_rejected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "empty_allowed_paths"

    def test_only_empty_string_rejected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=("",)),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "empty_allowed_paths"

    def test_only_whitespace_rejected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=("   ",)),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "empty_allowed_paths"

    def test_mixed_empty_and_real_rejected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(allowed_paths=("src/", "")),),
        )
        with pytest.raises(PlanValidationError):
            PlanValidator().validate(plan)


class TestErrorMessageContent:
    def test_error_message_names_offending_task(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task(task_id="T-offender", allowed_paths=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert "T-offender" in str(excinfo.value)

    def test_error_message_includes_plan_id(self) -> None:
        plan = Plan(
            plan_id="P-fail",
            tasks=(_task(allowed_paths=()),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert "P-fail" in str(excinfo.value)

    def test_multiple_offenders_all_named(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task(task_id="T-good", allowed_paths=("src/",)),
                _task(task_id="T-bad-1", allowed_paths=()),
                _task(task_id="T-bad-2", allowed_paths=()),
            ),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        msg = str(excinfo.value)
        assert "T-bad-1" in msg
        assert "T-bad-2" in msg
        assert "T-good" not in msg


class TestReasonCodesCoverage:
    """The validator must surface stable reason codes per SPEC §15 reject list.

    SPEC §15 enumerates 6 reject reasons. Slice 5 covers three explicitly:
    circular_dependency, missing_validation, empty_allowed_paths. Missing
    requirements + missing dependencies + invalid ordering are sibling
    concerns also covered by ``PlanValidator`` — they have their own tests.
    """

    def test_reason_codes_are_stable_strings(self) -> None:
        """Each reject reason must have a stable, snake_case code."""
        codes = {
            "missing_validation": PlanValidationError(
                plan_id="P", reason="missing_validation", task_ids=()
            ).reason,
            "empty_allowed_paths": PlanValidationError(
                plan_id="P", reason="empty_allowed_paths", task_ids=()
            ).reason,
            "circular_dependency": PlanValidationError(
                plan_id="P", reason="circular_dependency", task_ids=()
            ).reason,
            "missing_dependency": PlanValidationError(
                plan_id="P", reason="missing_dependency", task_ids=()
            ).reason,
            "missing_requirements": PlanValidationError(
                plan_id="P", reason="missing_requirements", task_ids=()
            ).reason,
            "invalid_ordering": PlanValidationError(
                plan_id="P", reason="invalid_ordering", task_ids=()
            ).reason,
        }
        assert codes == {
            "missing_validation",
            "empty_allowed_paths",
            "circular_dependency",
            "missing_dependency",
            "missing_requirements",
            "invalid_ordering",
        }