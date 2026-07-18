"""Traceability artifacts and plan validators (slice 5).

Per SPEC §15 ("Planning must produce bounded tasks with: ...
requirement traceability / scenario traceability") and §28
(slice 5 RED bullets):

    scenarios trace to requirements
    plans with missing validation fail
    circular task dependencies fail
    empty allowed paths fail

This module is closed-schema and side-effect-free. It is consumed by the
phase executors (slice 6+) which call ``PlanValidator.validate(plan)``
before any task execution begins.

Validator order (each independently raising ``PlanValidationError``):

    1. missing_validation       — task has no validation_commands
    2. empty_allowed_paths      — task has no allowed_paths (or only blanks)
    3. missing_requirements     — task has no requirement_ids
    4. missing_dependency       — task depends on a non-existent task_id
    5. circular_dependency      — dependency graph contains a cycle
    6. invalid_ordering         — reserved for future slice (depth ordering)

A plan that fails any check is rejected; the workflow must not advance
to PLANNING → EXECUTION without a passing validator result.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Reason codes — stable strings for downstream telemetry / events.jsonl.
# ---------------------------------------------------------------------------
ReasonCode = Literal[
    "missing_validation",
    "empty_allowed_paths",
    "missing_requirements",
    "missing_dependency",
    "circular_dependency",
    "invalid_ordering",
]


# ---------------------------------------------------------------------------
# Plan / Task data model
# ---------------------------------------------------------------------------


class RequirementTrace(BaseModel):
    """A single (requirement, scenarios) pair on a task.

    The traceability matrix is built by collecting every ``RequirementTrace``
    across tasks. A requirement that has no scenarios attached anywhere
    is "missing scenarios"; a scenario attached to a requirement-empty
    trace is "orphan".
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    requirement_id: str = Field(min_length=1)
    scenario_ids: tuple[str, ...] = Field(default_factory=tuple)


class Task(BaseModel):
    """One bounded unit of work in a plan.

    Per SPEC §15 "Implementation task":
    - task ID
    - objective
    - dependencies
    - allowed paths
    - prohibited paths (reserved for slice 6 — not yet used)
    - expected files (reserved for slice 6)
    - requirement traces  (paired requirement_id → scenarios)
    - constraints (free-form text; reserved for slice 6)
    - validation commands
    - dependency-change permission (reserved for slice 6)
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    task_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    requirement_traces: tuple[RequirementTrace, ...] = Field(default_factory=tuple)
    allowed_paths: tuple[str, ...] = Field(default_factory=tuple)
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    validation_commands: tuple[str, ...] = Field(default_factory=tuple)

    # Convenience accessors for the common case where the caller only needs
    # the flat lists (e.g. for serialization / event-log emission).
    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(t.requirement_id for t in self.requirement_traces)

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for t in self.requirement_traces:
            for s in t.scenario_ids:
                if s not in seen:
                    seen.append(s)
        return tuple(seen)


class Plan(BaseModel):
    """A complete plan produced by the planning phase."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    plan_id: str = Field(min_length=1)
    tasks: tuple[Task, ...] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Traceability report
# ---------------------------------------------------------------------------


class TraceabilityReport(BaseModel):
    """Structured summary of requirement / scenario coverage in a plan.

    The report is intentionally explicit (booleans + per-axis sets) so
    callers can branch on individual failures without re-running the
    validator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    referenced_requirements: frozenset[str] = Field(default_factory=frozenset)
    referenced_scenarios: frozenset[str] = Field(default_factory=frozenset)
    scenarios_by_requirement: dict[str, frozenset[str]] = Field(default_factory=dict)
    requirements_by_scenario: dict[str, frozenset[str]] = Field(default_factory=dict)
    is_complete: bool
    tasks_missing_requirements: frozenset[str] = Field(default_factory=frozenset)
    missing_scenarios: frozenset[str] = Field(default_factory=frozenset)
    unmapped_requirements: frozenset[str] = Field(default_factory=frozenset)
    orphan_scenarios: frozenset[str] = Field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Plan validation error
# ---------------------------------------------------------------------------


class PlanValidationError(Exception):
    """Raised when a plan fails any check in ``PlanValidator``."""

    #: Stable reason codes — keep in sync with ``ReasonCode`` Literal.
    _KNOWN_REASONS: frozenset[str] = frozenset(
        {
            "missing_validation",
            "empty_allowed_paths",
            "missing_requirements",
            "missing_dependency",
            "circular_dependency",
            "invalid_ordering",
        }
    )

    def __init__(
        self,
        *,
        plan_id: str,
        reason: str,
        task_ids: tuple[str, ...] = (),
        detail: str = "",
    ) -> None:
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError(f"plan_id must be a non-empty string, got {plan_id!r}")
        if reason not in self._KNOWN_REASONS:
            raise ValueError(f"reason must be one of {sorted(self._KNOWN_REASONS)}, got {reason!r}")
        self.plan_id = plan_id
        self.reason = reason
        self.task_ids = task_ids
        self.detail = detail
        super().__init__(self._format())

    def _format(self) -> str:
        if self.task_ids:
            offenders = ", ".join(self.task_ids)
            base = (
                f"plan {self.plan_id!r} rejected: reason={self.reason}; "
                f"offending tasks: {offenders}"
            )
        else:
            base = f"plan {self.plan_id!r} rejected: reason={self.reason}"
        if self.detail:
            base = f"{base}; detail: {self.detail}"
        return base


# ---------------------------------------------------------------------------
# Traceability validator
# ---------------------------------------------------------------------------


def build_traceability_report(plan: Plan) -> TraceabilityReport:
    """Build a coverage report without raising. Validation is done by ``TraceabilityValidator``."""
    scenarios_by_requirement: dict[str, set[str]] = {}
    requirements_by_scenario: dict[str, set[str]] = {}
    referenced_requirements: set[str] = set()
    referenced_scenarios: set[str] = set()
    for task in plan.tasks:
        for trace in task.requirement_traces:
            referenced_requirements.add(trace.requirement_id)
            scenarios_by_requirement.setdefault(trace.requirement_id, set()).update(
                trace.scenario_ids
            )
            for sid in trace.scenario_ids:
                referenced_scenarios.add(sid)
                requirements_by_scenario.setdefault(sid, set()).add(trace.requirement_id)
    return TraceabilityReport(
        referenced_requirements=frozenset(referenced_requirements),
        referenced_scenarios=frozenset(referenced_scenarios),
        scenarios_by_requirement={k: frozenset(v) for k, v in scenarios_by_requirement.items()},
        requirements_by_scenario={k: frozenset(v) for k, v in requirements_by_scenario.items()},
        is_complete=True,  # mutated by validator if checks fail
        tasks_missing_requirements=frozenset(),
        missing_scenarios=frozenset(),
        unmapped_requirements=frozenset(),
        orphan_scenarios=frozenset(),
    )


class TraceabilityValidator:
    """Validates the traceability aspect of a plan without raising.

    Returns a ``TraceabilityReport`` whose ``is_complete`` is False when any
    requirement is missing scenarios or any scenario is orphaned. The
    validator never raises — callers compose this with ``PlanValidator``.
    """

    def validate(self, plan: Plan) -> TraceabilityReport:
        report = build_traceability_report(plan)

        tasks_missing_requirements: set[str] = set()
        for task in plan.tasks:
            if not task.requirement_traces:
                tasks_missing_requirements.add(task.task_id)

        # Missing scenarios: requirement referenced but no scenarios cover it.
        missing_scenarios = {
            rid for rid, scns in report.scenarios_by_requirement.items() if not scns
        }

        # Orphans: scenarios that don't trace to any requirement.
        orphan_scenarios = {
            sid for sid, rids in report.requirements_by_scenario.items() if not rids
        }

        is_complete = (
            not tasks_missing_requirements and not missing_scenarios and not orphan_scenarios
        )

        # Pydantic models are frozen — rebuild with mutated values.
        return TraceabilityReport(
            referenced_requirements=report.referenced_requirements,
            referenced_scenarios=report.referenced_scenarios,
            scenarios_by_requirement=report.scenarios_by_requirement,
            requirements_by_scenario=report.requirements_by_scenario,
            is_complete=is_complete,
            tasks_missing_requirements=frozenset(tasks_missing_requirements),
            missing_scenarios=frozenset(missing_scenarios),
            unmapped_requirements=frozenset(missing_scenarios),
            orphan_scenarios=frozenset(orphan_scenarios),
        )


# ---------------------------------------------------------------------------
# Dependency-graph cycle detection
# ---------------------------------------------------------------------------


def find_dependency_cycles(plan: Plan) -> list[tuple[str, ...]]:
    """Return every dependency cycle in the plan as a tuple of task_ids.

    Each returned tuple is a cycle path (e.g. ``("T-1", "T-2", "T-1")``).
    An empty list means the graph is acyclic. Self-loops are reported as
    ``(task_id, task_id)``.
    """
    graph: dict[str, tuple[str, ...]] = {task.task_id: task.depends_on for task in plan.tasks}
    cycles: list[tuple[str, ...]] = []

    def dfs(node: str, path: tuple[str, ...]) -> None:
        if node in path:
            # Found a cycle — extract the loop.
            idx = path.index(node)
            cycles.append((*path[idx:], node))
            return
        if node not in graph:
            return  # missing dependency — caught by PlanValidator
        for nxt in graph[node]:
            dfs(nxt, (*path, node))

    for task_id in graph:
        dfs(task_id, ())
    return cycles


# ---------------------------------------------------------------------------
# Plan validator (the public entry point for the planning phase)
# ---------------------------------------------------------------------------


class PlanValidator:
    """Validate a plan against the SPEC §15 reject list.

    Each rule raises ``PlanValidationError`` with a stable ``reason`` code
    and the set of offending task_ids (when applicable). Rules run in a
    fixed order — the first failure aborts the validation, mirroring how
    downstream workflow code expects to handle errors.
    """

    def validate(self, plan: Plan) -> None:
        self._check_missing_validation(plan)
        self._check_empty_allowed_paths(plan)
        self._check_missing_requirements(plan)
        self._check_missing_dependencies(plan)
        self._check_no_cycles(plan)

    def _check_missing_validation(self, plan: Plan) -> None:
        offenders = tuple(
            t.task_id for t in plan.tasks if not any(cmd.strip() for cmd in t.validation_commands)
        )
        if offenders:
            raise PlanValidationError(
                plan_id=plan.plan_id,
                reason="missing_validation",
                task_ids=offenders,
            )

    def _check_empty_allowed_paths(self, plan: Plan) -> None:
        offenders = tuple(
            t.task_id for t in plan.tasks if not any(p.strip() for p in t.allowed_paths)
        )
        if offenders:
            raise PlanValidationError(
                plan_id=plan.plan_id,
                reason="empty_allowed_paths",
                task_ids=offenders,
            )

    def _check_missing_requirements(self, plan: Plan) -> None:
        offenders = tuple(t.task_id for t in plan.tasks if not t.requirement_traces)
        if offenders:
            raise PlanValidationError(
                plan_id=plan.plan_id,
                reason="missing_requirements",
                task_ids=offenders,
            )

    def _check_missing_dependencies(self, plan: Plan) -> None:
        known = {t.task_id for t in plan.tasks}
        missing_pairs: list[str] = []
        for t in plan.tasks:
            for dep in t.depends_on:
                if dep not in known:
                    missing_pairs.append(f"{t.task_id}->{dep}")
        if missing_pairs:
            raise PlanValidationError(
                plan_id=plan.plan_id,
                reason="missing_dependency",
                detail="; ".join(missing_pairs),
            )

    def _check_no_cycles(self, plan: Plan) -> None:
        cycles = find_dependency_cycles(plan)
        if cycles:
            rendered = "; ".join("->".join(cycle) for cycle in cycles)
            raise PlanValidationError(
                plan_id=plan.plan_id,
                reason="circular_dependency",
                detail=rendered,
            )


__all__ = [
    "Plan",
    "PlanValidationError",
    "PlanValidator",
    "ReasonCode",
    "Task",
    "TraceabilityReport",
    "TraceabilityValidator",
    "build_traceability_report",
    "find_dependency_cycles",
]
