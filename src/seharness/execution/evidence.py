"""Evidence primitives for slice 6 (TDD-aware task execution).

Per SPEC \u00a7"TDD evidence", every implementation task produces:

    execution/<task-id>/
        red/{command.txt, stdout.txt, stderr.txt, result.json}
        green/{command.txt, stdout.txt, stderr.txt, result.json}
        task-result.json

This module owns the *types* \u2014 ``RedResult`` and ``GreenResult`` Pydantic
models, the ``FailureKind`` enum, and the ``TaskEvidenceLayout`` helper
that locates files on disk. The actual writing happens in the runner
hook passed to ``TaskExecutionService`` (slice 6 service).

Design notes:

- Pydantic models use ``ConfigDict(extra=\"forbid\", frozen=True)`` so
  typos surface immediately and the JSON on disk is the canonical
  shape. ``validate_assignment=True`` is omitted because the models
  are frozen.
- ``FailureKind`` is a ``StrEnum`` so values are stable strings on
  disk; the validator only accepts the documented buckets.
- ``TaskEvidenceLayout`` is a value object: no I/O, just paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class FailureKind(StrEnum):
    """Closed set of failure reasons for RED evidence.

    Per SPEC \u00a7"TDD evidence", RED must fail for the expected reason.
    The harness recognises four buckets; everything else is rejected
    as "unknown failure kind" so the runner cannot smuggle in a fake
    passing test.
    """

    EXPECTED_FAILURE = "expected_failure"
    UNRELATED_FAILURE = "unrelated_failure"
    COLLECTION_ERROR = "collection_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class _BaseResult(BaseModel):
    """Common shape for RED/GREEN evidence results.

    Both ``RedResult`` and ``GreenResult`` share these fields. The
    ``phase`` discriminator lets the validator pick the right model
    from raw JSON without trusting an ad-hoc ``type`` field.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: str = Field(min_length=1)
    exit_code: int = Field(ge=-1)
    duration_s: float = Field(ge=0)
    test_id: str = Field(min_length=1)
    command: str = Field(min_length=1)


class RedResult(_BaseResult):
    """RED evidence result. ``exit_code`` must be non-zero when
    ``failure_kind`` is set; tests rely on this invariant."""

    failure_kind: FailureKind | None = None
    failure_reason: str | None = Field(default=None, max_length=4096)


class GreenResult(_BaseResult):
    """GREEN evidence result. ``exit_code`` must be 0."""

    covered_tests: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskEvidenceLayout:
    """Filesystem layout for a single task's evidence bundle.

    Pure value object: no I/O. Use ``task_dir`` / ``red_dir`` /
    ``green_dir`` properties to compute paths; let the validator
    and runner do the actual writes.
    """

    task_id: str
    root: Path

    @property
    def task_dir(self) -> Path:
        """execution/<task_id>/"""

        return self.root / "execution" / self.task_id

    @property
    def red_dir(self) -> Path:
        return self.task_dir / "red"

    @property
    def green_dir(self) -> Path:
        return self.task_dir / "green"

    @property
    def task_result_path(self) -> Path:
        return self.task_dir / "task-result.json"
