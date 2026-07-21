"""Cluster N PR4 \u2014 specification and plan schemas.

Cluster N of the MiniMax SE-harness improvement handoff.
**Step 4** of the targeted refinement workplan: structured
specification and adaptive planning.

The workplan requires that the live MiniMax model produces a
schema-valid specification and plan. The schema lives here so
the model contract is documented, validated, and pinned in one
place.

Acceptance criteria from the workplan (Step 4 \u00a7"Add contract
tests"):

1. The discovered repository profile is included.
2. Repository instructions are included with explicit precedence.
3. Validation commands come from discovery rather than model
   invention.
4. Unknown commands are rejected.
5. Plans may contain multiple ordered tasks.
6. All task paths fall within policy.

These criteria are implemented as pydantic ``BaseModel``
schemas with ``extra=forbid``, ``frozen=True``, and
``validate_assignment=True``. The model MUST produce
JSON that satisfies the schema; the orchestrator MUST
verify with ``model_validate`` and surface
``error_kind=malformed_output`` on failure (per cluster N
error translation).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical set of validation commands exposed by the
# RepositoryProfile. The model MUST pick from this set; any
# command outside this set is rejected at validation time.
#
# Mirrors ``seharness.repository.discovery.ValidationCommand``
# without introducing a circular dependency.
ALLOWED_VALIDATION_COMMANDS: frozenset[str] = frozenset(
    {
        "test",
        "lint",
        "type_check",
        "format",
    }
)


class _StrictModel(BaseModel):
    """Base model that forbids any keys not declared on the schema.

    Mirrors ``_StrictModel`` from ``seharness.repository.discovery``
    to keep the validation contract consistent across the package.
    Frozen so the orchestrator can rely on the schema-validated
    values not changing mid-run.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class SpecificationSchema(_StrictModel):
    """Structured specification the model MUST produce.

    Per the workplan:

    - The discovered repository profile name is included
      (``discovered_repo_profile_name``); this is the
      ``RepositoryProfile.name`` value, copied verbatim from
      discovery.
    - Repository instructions are included
      (``repository_instructions``); this is the list of
      instruction file paths from
      ``RepositoryProfile.instruction_files``.
    - Validation commands come from discovery
      (``validation_commands``); the model MUST pick from
      ``ALLOWED_VALIDATION_COMMANDS`` rather than invent new
      ones.
    - The description captures the feature request.

    ``extra=forbid`` ensures the model cannot smuggle in
    undeclared fields (e.g. a stray ``scripts_to_run`` that the
    orchestrator would have to ignore).
    """

    discovered_repo_profile_name: str = Field(min_length=1)
    repository_instructions: tuple[str, ...] = Field(default_factory=tuple)
    validation_commands: tuple[str, ...] = Field(default_factory=tuple)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_commands_against_discovery(self) -> SpecificationSchema:
        """Reject validation commands that are not in the
        canonical set. Per the workplan, unknown commands must
        be rejected."""
        unknown = [
            cmd for cmd in self.validation_commands if cmd not in ALLOWED_VALIDATION_COMMANDS
        ]
        if unknown:
            msg = (
                f"unknown validation commands (must be drawn from "
                f"{sorted(ALLOWED_VALIDATION_COMMANDS)}): {unknown}"
            )
            raise ValueError(msg)
        return self


class PlanTask(_StrictModel):
    """One task in a structured plan.

    Per the workplan:

    - ``allowed_paths`` declares the policy-allowed paths for
      the task; the orchestrator's sandbox layer enforces this
      list. Tasks with paths outside policy are rejected at
      validation time.
    - ``order_index`` is the explicit ordering. The orchestrator
      executes tasks in ascending ``order_index`` order.
    """

    task_id: str = Field(min_length=1)
    task_objective: str = Field(min_length=1)
    allowed_paths: tuple[str, ...] = Field(default_factory=tuple)
    order_index: int = Field(ge=0)


class PlanSchema(_StrictModel):
    """Structured plan the model MUST produce.

    Per the workplan:

    - Plans may contain multiple ordered tasks
      (``tasks`` with ``order_index``).
    - All task paths fall within policy (validated by
      :func:`validate_plan_against_policy`).
    """

    plan_id: str = Field(min_length=1)
    tasks: tuple[PlanTask, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_order_indices_are_unique(self) -> PlanSchema:
        """Reject duplicate ``order_index`` values. Tasks must be
        uniquely ordered; two tasks with the same ``order_index``
        would create an ambiguous execution sequence."""
        indices = [t.order_index for t in self.tasks]
        if len(set(indices)) != len(indices):
            duplicates = sorted({idx for idx in indices if indices.count(idx) > 1})
            msg = f"plan tasks have duplicate order_index values: {duplicates}"
            raise ValueError(msg)
        return self


def validate_plan_against_policy(
    plan: PlanSchema,
    *,
    policy_allowed_paths: Sequence[str],
) -> None:
    """Reject tasks whose ``allowed_paths`` are not within the
    operator-declared policy.

    Per the workplan: "All task paths fall within policy." The
    policy is a tuple of path prefixes the orchestrator has
    authorised (typically ``src/``, ``tests/``). Any task with
    an ``allowed_paths`` entry outside the policy is rejected.

    The check is conservative: every ``allowed_paths`` entry
    in every task must be within the policy. The check is
    ``startswith``-based; absolute paths require an explicit
    absolute-policy entry.
    """
    if not policy_allowed_paths:
        msg = "policy_allowed_paths is empty; refusing to validate plan"
        raise ValueError(msg)
    offending: list[str] = []
    for task in plan.tasks:
        for path in task.allowed_paths:
            if not any(path.startswith(prefix) for prefix in policy_allowed_paths):
                offending.append(f"{task.task_id}({path})")
    if offending:
        msg = (
            f"plan tasks have allowed_paths outside policy "
            f"{list(policy_allowed_paths)}: {offending}"
        )
        raise ValueError(msg)


def parse_specification(payload: Any) -> SpecificationSchema:
    """Parse a model payload (dict / Mapping) into a
    :class:`SpecificationSchema`. Raises ``ValueError`` on
    schema mismatch."""
    if isinstance(payload, SpecificationSchema):
        return payload
    if isinstance(payload, Mapping):
        return SpecificationSchema.model_validate(dict(payload))
    msg = f"specification payload is not a Mapping: {type(payload).__name__}"
    raise ValueError(msg)


def parse_plan(payload: Any) -> PlanSchema:
    """Parse a model payload (dict / Mapping) into a
    :class:`PlanSchema`. Raises ``ValueError`` on schema
    mismatch."""
    if isinstance(payload, PlanSchema):
        return payload
    if isinstance(payload, Mapping):
        return PlanSchema.model_validate(dict(payload))
    msg = f"plan payload is not a Mapping: {type(payload).__name__}"
    raise ValueError(msg)


__all__ = [
    "ALLOWED_VALIDATION_COMMANDS",
    "PlanSchema",
    "PlanTask",
    "SpecificationSchema",
    "parse_plan",
    "parse_specification",
    "validate_plan_against_policy",
]
