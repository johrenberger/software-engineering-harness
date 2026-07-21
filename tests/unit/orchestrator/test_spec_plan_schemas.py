"""Cluster N PR4 \u2014 specification and plan schema contract tests.

Pins the workplan Step 4 acceptance criteria:

1. The discovered repository profile is included.
2. Repository instructions are included with explicit precedence.
3. Validation commands come from discovery rather than model
   invention.
4. Unknown commands are rejected.
5. Plans may contain multiple ordered tasks.
6. All task paths fall within policy.

Each criterion is exercised by a named test class. The tests
deliberately use the schema and policy validators directly
(``parse_specification``, ``parse_plan``,
``validate_plan_against_policy``) rather than driving the
full ``ModelBackedSpecificationService`` / ``ModelBackedPlanningService``;
that keeps the contract tests fast and free of model-router
plumbing. The service-level integration tests live in
``test_services.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# Trigger the canonical import order before importing
# ``seharness.orchestrator.spec_plan_schemas``. The pre-existing
# controller → orchestrator circular import requires that
# ``seharness.controller.run_ledger`` be initialised first.
import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.orchestrator.spec_plan_schemas import (
    ALLOWED_VALIDATION_COMMANDS,
    PlanSchema,
    PlanTask,
    SpecificationSchema,
    parse_plan,
    parse_specification,
    validate_plan_against_policy,
)

# ---------------------------------------------------------------------------
# Spec schema contract
# ---------------------------------------------------------------------------


class TestSpecificationSchemaContract:
    """Workplan criterion 1: discovered repository profile is
    included. The schema REQUIRES ``discovered_repo_profile_name``
    with ``min_length=1`` so a missing or empty name is rejected."""

    def test_requires_discovered_repo_profile_name(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SpecificationSchema(
                description="x",
                validation_commands=("test",),
            )
        assert "discovered_repo_profile_name" in str(excinfo.value)

    def test_rejects_empty_repo_profile_name(self) -> None:
        with pytest.raises(ValidationError):
            SpecificationSchema(
                discovered_repo_profile_name="",
                description="x",
                validation_commands=("test",),
            )

    def test_accepts_valid_spec(self) -> None:
        spec = SpecificationSchema(
            discovered_repo_profile_name="software-engineering-harness",
            repository_instructions=("AGENTS.md", "CLAUDE.md"),
            validation_commands=("test", "lint", "type_check"),
            description="add cluster N readiness validation",
        )
        assert spec.discovered_repo_profile_name == "software-engineering-harness"
        assert spec.repository_instructions == ("AGENTS.md", "CLAUDE.md")
        assert spec.validation_commands == ("test", "lint", "type_check")


class TestSpecificationSchemaRepositoryInstructions:
    """Workplan criterion 2: repository instructions are included
    with explicit precedence. The schema accepts a tuple of
    instruction file paths in discovery-precedence order; the
    tuple is required to be ordered consistently with the
    discovered profile."""

    def test_instructions_are_optional(self) -> None:
        spec = SpecificationSchema(
            discovered_repo_profile_name="x",
            description="y",
            validation_commands=("test",),
        )
        assert spec.repository_instructions == ()

    def test_instructions_preserve_order(self) -> None:
        spec = SpecificationSchema(
            discovered_repo_profile_name="x",
            repository_instructions=(
                "AGENTS.md",
                "CLAUDE.md",
                "README.md",
            ),
            description="y",
            validation_commands=("test",),
        )
        # Order is preserved end-to-end; the orchestrator reads
        # instructions in tuple order.
        assert spec.repository_instructions == (
            "AGENTS.md",
            "CLAUDE.md",
            "README.md",
        )


class TestSpecificationSchemaRejectsUnknownCommands:
    """Workplan criterion 3 + 4: validation commands come from
    discovery rather than model invention; unknown commands are
    rejected. The schema enforces this via a ``model_validator``
    that requires every command to be in
    ``ALLOWED_VALIDATION_COMMANDS``."""

    def test_allowed_commands_are_canonical(self) -> None:
        # The canonical set is small on purpose: it mirrors
        # ``RepositoryProfile.validation_commands`` keys.
        assert frozenset({"test", "lint", "type_check", "format"}) == ALLOWED_VALIDATION_COMMANDS

    def test_unknown_command_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SpecificationSchema(
                discovered_repo_profile_name="x",
                description="y",
                validation_commands=("test", "rm -rf /"),  # nosec B607
            )
        msg = str(excinfo.value)
        assert "unknown validation commands" in msg
        assert "rm -rf /" in msg

    def test_command_invented_by_model_is_rejected(self) -> None:
        """A common failure mode: the model invents a command
        that wasn't in the discovered profile. The schema
        rejects it without the orchestrator having to do any
        string-matching."""

        with pytest.raises(ValidationError):
            SpecificationSchema(
                discovered_repo_profile_name="x",
                description="y",
                validation_commands=("test", "make all-the-things"),
            )

    def test_empty_command_list_is_accepted(self) -> None:
        """Repositories with no discovered validation commands
        are rare but valid (e.g. a fresh scaffold). The schema
        accepts an empty command list."""

        spec = SpecificationSchema(
            discovered_repo_profile_name="x",
            description="y",
            validation_commands=(),
        )
        assert spec.validation_commands == ()

    def test_extra_keys_rejected(self) -> None:
        """The schema is ``extra=forbid`` so the model cannot
        smuggle in undeclared fields (e.g. ``scripts_to_run``)."""

        with pytest.raises(ValidationError):
            SpecificationSchema(
                discovered_repo_profile_name="x",
                description="y",
                validation_commands=("test",),
                scripts_to_run=("deploy.sh",),
            )


class TestParseSpecificationHelper:
    """The :func:`parse_specification` helper accepts a
    ``dict`` / ``Mapping`` and returns a validated schema. It
    raises ``ValueError`` on schema mismatch."""

    def test_accepts_schema_instance(self) -> None:
        spec = SpecificationSchema(
            discovered_repo_profile_name="x",
            description="y",
            validation_commands=("test",),
        )
        assert parse_specification(spec) is spec

    def test_accepts_mapping(self) -> None:
        spec = parse_specification(
            {
                "discovered_repo_profile_name": "x",
                "description": "y",
                "validation_commands": ["test"],
            }
        )
        assert isinstance(spec, SpecificationSchema)

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="not a Mapping"):
            parse_specification("not a dict")

    def test_rejects_schema_mismatch(self) -> None:
        with pytest.raises(ValueError, match="discovered_repo_profile_name"):
            parse_specification({"description": "y", "validation_commands": []})


# ---------------------------------------------------------------------------
# Plan schema contract
# ---------------------------------------------------------------------------


class TestPlanSchemaAcceptsMultipleOrderedTasks:
    """Workplan criterion 5: plans may contain multiple ordered
    tasks. The schema accepts a tuple of :class:`PlanTask`
    with an explicit ``order_index``."""

    def test_single_task_plan_is_valid(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="add readiness gate",
                    allowed_paths=("src/",),
                    order_index=0,
                ),
            ),
        )
        assert len(plan.tasks) == 1

    def test_multiple_tasks_with_distinct_order_indices(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="add schema",
                    allowed_paths=("src/",),
                    order_index=0,
                ),
                PlanTask(
                    task_id="t2",
                    task_objective="add validator",
                    allowed_paths=("src/",),
                    order_index=1,
                ),
                PlanTask(
                    task_id="t3",
                    task_objective="add tests",
                    allowed_paths=("tests/",),
                    order_index=2,
                ),
            ),
        )
        assert len(plan.tasks) == 3
        indices = [t.order_index for t in plan.tasks]
        assert sorted(indices) == [0, 1, 2]

    def test_rejects_empty_plan(self) -> None:
        with pytest.raises(ValidationError):
            PlanSchema(plan_id="p1", tasks=())

    def test_rejects_duplicate_order_indices(self) -> None:
        """Two tasks with the same ``order_index`` create an
        ambiguous execution sequence; the schema rejects."""

        with pytest.raises(ValidationError) as excinfo:
            PlanSchema(
                plan_id="p1",
                tasks=(
                    PlanTask(
                        task_id="t1",
                        task_objective="a",
                        order_index=0,
                    ),
                    PlanTask(
                        task_id="t2",
                        task_objective="b",
                        order_index=0,
                    ),
                ),
            )
        assert "duplicate order_index" in str(excinfo.value)

    def test_rejects_negative_order_index(self) -> None:
        with pytest.raises(ValidationError):
            PlanTask(
                task_id="t1",
                task_objective="x",
                order_index=-1,
            )


class TestPlanPolicyEnforcesAllowedPaths:
    """Workplan criterion 6: all task paths fall within policy.
    :func:`validate_plan_against_policy` walks every task and
    rejects any whose ``allowed_paths`` contains an entry
    outside the operator-declared policy."""

    def test_in_policy_paths_are_accepted(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="edit src",
                    allowed_paths=("src/", "tests/"),
                    order_index=0,
                ),
            ),
        )
        # No exception.
        validate_plan_against_policy(plan, policy_allowed_paths=("src/", "tests/"))

    def test_out_of_policy_paths_are_rejected(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="deploy",
                    allowed_paths=("deploy/",),  # outside policy
                    order_index=0,
                ),
            ),
        )
        with pytest.raises(ValueError) as excinfo:
            validate_plan_against_policy(plan, policy_allowed_paths=("src/", "tests/"))
        msg = str(excinfo.value)
        assert "t1(deploy/)" in msg
        assert "outside policy" in msg

    def test_mixed_paths_report_every_offending_task(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="edit src",
                    allowed_paths=("src/",),
                    order_index=0,
                ),
                PlanTask(
                    task_id="t2",
                    task_objective="deploy",
                    allowed_paths=("deploy/",),
                    order_index=1,
                ),
                PlanTask(
                    task_id="t3",
                    task_objective="backup",
                    allowed_paths=("/etc/",),
                    order_index=2,
                ),
            ),
        )
        with pytest.raises(ValueError) as excinfo:
            validate_plan_against_policy(plan, policy_allowed_paths=("src/", "tests/"))
        msg = str(excinfo.value)
        assert "t2(deploy/)" in msg
        assert "t3(/etc/)" in msg
        # t1 is fine; not in the offending list.
        assert "t1(" not in msg

    def test_empty_policy_rejects_everything(self) -> None:
        """An empty policy is a programming error: the operator
        must declare at least one allowed path. The validator
        rejects this rather than silently passing."""

        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="x",
                    allowed_paths=("src/",),
                    order_index=0,
                ),
            ),
        )
        with pytest.raises(ValueError, match="policy_allowed_paths is empty"):
            validate_plan_against_policy(plan, policy_allowed_paths=())

    def test_task_with_empty_allowed_paths_is_accepted(self) -> None:
        """A task with no ``allowed_paths`` (e.g. a planning
        task that does not touch the filesystem) passes the
        policy check trivially."""

        plan = PlanSchema(
            plan_id="p1",
            tasks=(
                PlanTask(
                    task_id="t1",
                    task_objective="research",
                    allowed_paths=(),
                    order_index=0,
                ),
            ),
        )
        validate_plan_against_policy(plan, policy_allowed_paths=("src/", "tests/"))


class TestParsePlanHelper:
    """The :func:`parse_plan` helper accepts a ``dict`` /
    ``Mapping`` and returns a validated schema. Raises
    ``ValueError`` on schema mismatch."""

    def test_accepts_schema_instance(self) -> None:
        plan = PlanSchema(
            plan_id="p1",
            tasks=(PlanTask(task_id="t1", task_objective="x", order_index=0),),
        )
        assert parse_plan(plan) is plan

    def test_accepts_mapping(self) -> None:
        plan = parse_plan(
            {
                "plan_id": "p1",
                "tasks": [
                    {
                        "task_id": "t1",
                        "task_objective": "x",
                        "allowed_paths": ["src/"],
                        "order_index": 0,
                    }
                ],
            }
        )
        assert isinstance(plan, PlanSchema)
        assert plan.plan_id == "p1"
        assert len(plan.tasks) == 1

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="not a Mapping"):
            parse_plan([1, 2, 3])

    def test_rejects_missing_required_field(self) -> None:
        with pytest.raises(ValueError):
            parse_plan(
                {
                    "tasks": [
                        {
                            "task_id": "t1",
                            "task_objective": "x",
                            "order_index": 0,
                        }
                    ],
                }
            )
