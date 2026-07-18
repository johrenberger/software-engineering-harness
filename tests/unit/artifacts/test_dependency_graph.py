"""RED — Slice 5 behavior 04: circular task dependencies fail.

Per SPEC §15 ("Reject a plan when: tasks have circular dependencies") and
§28 (slice 5 RED bullets):

    circular task dependencies fail

The dependency-graph cycle detector must:
- accept a DAG (no error)
- reject a self-loop (T-1 → T-1)
- reject a 2-cycle (T-1 → T-2 → T-1)
- reject a longer cycle (T-1 → T-2 → T-3 → T-1)
- report the offending cycle path in the exception message
"""

from __future__ import annotations

import pytest

from seharness.artifacts.traceability import (
    Plan,
    PlanValidationError,
    PlanValidator,
    RequirementTrace,
    Task,
    find_dependency_cycles,
)


def _trace(requirement_id: str, scenario_ids: tuple[str, ...] = ()) -> RequirementTrace:
    return RequirementTrace(requirement_id=requirement_id, scenario_ids=scenario_ids)


def _task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    requirement_traces: tuple[RequirementTrace, ...] = (_trace("FR-1", ("SCN-1",)),),
    allowed_paths: tuple[str, ...] = ("src/",),
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


class TestAcyclicGraph:
    def test_no_cycles_in_dag(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1"),
                _task("T-2", depends_on=("T-1",)),
                _task("T-3", depends_on=("T-2",)),
            ),
        )
        assert find_dependency_cycles(plan) == []

    def test_no_cycles_with_diamond_dependency(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1"),
                _task("T-2", depends_on=("T-1",)),
                _task("T-3", depends_on=("T-1",)),
                _task("T-4", depends_on=("T-2", "T-3")),
            ),
        )
        assert find_dependency_cycles(plan) == []

    def test_no_cycles_in_disconnected_components(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1"),
                _task("T-2", depends_on=("T-1",)),
                _task("T-3"),
                _task("T-4", depends_on=("T-3",)),
            ),
        )
        assert find_dependency_cycles(plan) == []


class TestSelfLoop:
    def test_self_loop_detected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task("T-1", depends_on=("T-1",)),),
        )
        cycles = find_dependency_cycles(plan)
        assert len(cycles) >= 1
        assert any("T-1" in cycle for cycle in cycles)


class TestTwoCycle:
    def test_two_cycle_detected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1", depends_on=("T-2",)),
                _task("T-2", depends_on=("T-1",)),
            ),
        )
        cycles = find_dependency_cycles(plan)
        assert len(cycles) >= 1
        assert any(set(cycle) == {"T-1", "T-2"} for cycle in cycles)


class TestLongerCycle:
    def test_three_cycle_detected(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1", depends_on=("T-2",)),
                _task("T-2", depends_on=("T-3",)),
                _task("T-3", depends_on=("T-1",)),
            ),
        )
        cycles = find_dependency_cycles(plan)
        assert len(cycles) >= 1
        assert any(set(cycle) == {"T-1", "T-2", "T-3"} for cycle in cycles)


class TestValidatorRejectsCycles:
    def test_validator_rejects_self_loop(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task("T-1", depends_on=("T-1",)),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "circular_dependency"

    def test_validator_rejects_two_cycle(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1", depends_on=("T-2",)),
                _task("T-2", depends_on=("T-1",)),
            ),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "circular_dependency"
        assert "T-1" in str(excinfo.value)

    def test_validator_rejects_long_cycle(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(
                _task("T-1", depends_on=("T-2",)),
                _task("T-2", depends_on=("T-3",)),
                _task("T-3", depends_on=("T-1",)),
            ),
        )
        with pytest.raises(PlanValidationError):
            PlanValidator().validate(plan)


class TestMissingDependency:
    def test_missing_dependency_raises(self) -> None:
        plan = Plan(
            plan_id="P-1",
            tasks=(_task("T-1", depends_on=("T-missing",)),),
        )
        with pytest.raises(PlanValidationError) as excinfo:
            PlanValidator().validate(plan)
        assert excinfo.value.reason == "missing_dependency"
        assert "T-missing" in str(excinfo.value)
