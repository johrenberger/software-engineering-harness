"""Phase sequencing for the canonical orchestrator (SPEC §"Phase 8").

This module is intentionally data-only — the orchestrator does the real
work; this file just declares the canonical sequence and a short
human-readable description for each phase.

Cluster A keeps the description table here so future phases (planning,
review) can extend it without touching the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

from seharness.orchestrator.types import DEFAULT_PHASE_SEQUENCE, PhaseName


@dataclass(frozen=True)
class _PhaseInfo:
    phase: PhaseName
    description: str
    #: True if a phase failure should stop the run (vs. recoverable).
    fatal_on_failure: bool


#: Internal registry of phase metadata. The orchestrator iterates this
#: table when running a workflow.
_PHASE_TABLE: dict[PhaseName, _PhaseInfo] = {
    PhaseName.FEATURE_REQUEST: _PhaseInfo(
        phase=PhaseName.FEATURE_REQUEST,
        description="Accept and validate the feature request.",
        fatal_on_failure=True,
    ),
    PhaseName.REPOSITORY_DISCOVERY: _PhaseInfo(
        phase=PhaseName.REPOSITORY_DISCOVERY,
        description="Profile the repository (slice 3).",
        fatal_on_failure=True,
    ),
    PhaseName.SPECIFICATION: _PhaseInfo(
        phase=PhaseName.SPECIFICATION,
        description="Derive a specification from the feature request.",
        fatal_on_failure=True,
    ),
    PhaseName.PLANNING: _PhaseInfo(
        phase=PhaseName.PLANNING,
        description="Produce a Plan with one bounded Task.",
        fatal_on_failure=True,
    ),
    PhaseName.IMPLEMENTATION: _PhaseInfo(
        phase=PhaseName.IMPLEMENTATION,
        description="Run the Task through TaskExecutionService (slice 7).",
        fatal_on_failure=True,
    ),
    PhaseName.VALIDATION: _PhaseInfo(
        phase=PhaseName.VALIDATION,
        description="Re-run the task's validation commands.",
        fatal_on_failure=False,
    ),
    PhaseName.REMEDIATION: _PhaseInfo(
        phase=PhaseName.REMEDIATION,
        description="Revert unauthorized changes; re-validate.",
        fatal_on_failure=False,
    ),
    PhaseName.REVIEW: _PhaseInfo(
        phase=PhaseName.REVIEW,
        description="Independent review verdict.",
        fatal_on_failure=False,
    ),
    PhaseName.DRAFT_PR: _PhaseInfo(
        phase=PhaseName.DRAFT_PR,
        description="Create a draft PR via PullRequestClient.",
        fatal_on_failure=False,
    ),
    PhaseName.CI: _PhaseInfo(
        phase=PhaseName.CI,
        description="Observe CI readiness via CiMonitor.",
        fatal_on_failure=False,
    ),
    PhaseName.READY: _PhaseInfo(
        phase=PhaseName.READY,
        description="Mark the run ready-for-review.",
        fatal_on_failure=False,
    ),
    PhaseName.COMPLETED: _PhaseInfo(
        phase=PhaseName.COMPLETED,
        description="Terminal completion.",
        fatal_on_failure=False,
    ),
}


#: Re-exported public phase sequence — what the orchestrator runs.
PHASE_SEQUENCE: tuple[PhaseName, ...] = DEFAULT_PHASE_SEQUENCE


def phase_info(phase: PhaseName) -> _PhaseInfo:
    return _PHASE_TABLE[phase]


__all__ = ["PHASE_SEQUENCE", "phase_info"]
