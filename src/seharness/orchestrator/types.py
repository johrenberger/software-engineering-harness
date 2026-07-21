"""Orchestrator value types.

All types here are frozen / immutable per the project's
domain-modeling conventions. They are passed through every phase
boundary so callers can rebuild run history from any single phase
event.

These types intentionally do NOT import from
``seharness.pipeline.vertical_slice`` — that module imports *from*
this one (preserves the cluster-A dependency direction).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NewType

from seharness.config import RuntimeProfile


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


RunId = NewType("RunId", str)


def new_run_id() -> RunId:
    """Mint a new run id (``orch-<8 hex chars>``)."""
    return RunId(f"orch-{uuid.uuid4().hex[:8]}")


class PhaseName(StrEnum):
    """The 12 phases named in SPEC §"Phase 8".

    StrEnum so callers can compare against string literals
    (e.g. ``phase == "validation"``) without explicit conversion.
    """

    FEATURE_REQUEST = "feature_request"
    REPOSITORY_DISCOVERY = "repository_discovery"
    SPECIFICATION = "specification"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    VALIDATION = "validation"
    REMEDIATION = "remediation"
    REVIEW = "review"
    DRAFT_PR = "draft_pr"
    CI = "ci"
    READY = "ready"
    COMPLETED = "completed"


#: Default phase ordering — what SPEC §"Phase 8" prescribes.
DEFAULT_PHASE_SEQUENCE: tuple[PhaseName, ...] = (
    PhaseName.FEATURE_REQUEST,
    PhaseName.REPOSITORY_DISCOVERY,
    PhaseName.SPECIFICATION,
    PhaseName.PLANNING,
    PhaseName.IMPLEMENTATION,
    PhaseName.VALIDATION,
    PhaseName.REMEDIATION,
    PhaseName.REVIEW,
    PhaseName.DRAFT_PR,
    PhaseName.CI,
    PhaseName.READY,
    PhaseName.COMPLETED,
)


class PhaseOutcome(StrEnum):
    """Outcome of a single phase invocation."""

    OK = "ok"
    SKIPPED = "skipped"  # phase not applicable to this run
    FAILED = "failed"
    BLOCKED = "blocked"  # policy violation that requires intervention
    PAUSED = "paused"  # awaiting external signal (resume / approval)


@dataclass(frozen=True)
class PhaseSpec:
    """Description of a single phase call.

    ``attempt`` is the 0-based retry counter; ``idempotency_key`` is
    deterministic for the same (run_id, phase, attempt) tuple, so the
    orchestrator can replay a phase safely (Cluster E, story E1).
    """

    run_id: RunId
    phase: PhaseName
    attempt: int = 0
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            object.__setattr__(
                self,
                "idempotency_key",
                f"{self.run_id}:{self.phase.value}:{self.attempt}",
            )


@dataclass(frozen=True)
class RunContext:
    """Snapshot of run state passed between phases.

    Carries the FeatureRequest, discovered repo profile, generated
    plan, and the artifacts produced so far. Frozen so phases can't
    mutate prior phase output silently — they return a new context.
    """

    run_id: RunId
    feature_description: str
    repo_path: str
    profile_path: str | None = None
    specification_path: str | None = None
    plan_id: str | None = None
    task_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    validation_exit_code: int | None = None
    review_verdict: str | None = None
    pr_url: str | None = None
    ci_outcome: str | None = None
    started_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class OrchestratorConfig:
    """Frozen orchestrator configuration.

    All knobs the operator needs to tune. Defaults are safe.
    """

    execution_root: str = ".openclaw-runs/orchestrator"
    auto_remediate: bool = True
    max_remediation_attempts: int = 3
    max_validation_attempts: int = 3
    pr_draft: bool = True
    #: When True, real subprocesses run for the validation phase.
    #: When False (default), the orchestrator uses the in-memory
    #: ``StubRunner`` so unit tests don't spawn subprocesses.
    use_real_subprocess: bool = False
    # Cluster WP2: which runtime profile the orchestrator is operating
    # in. The default is DEVELOPMENT so notebook + local-iteration
    # callers (the bulk of today's users) are unaffected. Production
    # deployments must set ``runtime_profile=RuntimeProfile.PRODUCTION``
    # to get fail-closed adapter validation; see
    # :mod:`seharness.orchestrator.runtime_profile`.
    runtime_profile: RuntimeProfile = RuntimeProfile.DEVELOPMENT

    def __post_init__(self) -> None:
        # dataclass(frozen=True) + ConfigDict is awkward; we just
        # validate fields manually here.
        if self.max_remediation_attempts < 1:
            raise ValueError("max_remediation_attempts must be >= 1")
        if self.max_validation_attempts < 1:
            raise ValueError("max_validation_attempts must be >= 1")
