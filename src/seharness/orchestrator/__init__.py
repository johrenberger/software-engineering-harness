"""Canonical orchestrator package (Cluster A, story A2).

The orchestrator is the single workflow engine for the harness. It
composes the existing slice-3..slice-10 services in the SPEC §"Phase 8"
sequence and persists every phase transition to the controller's
``RunLedger``.

Entry points:

- ``Orchestrator.start_run(feature_request)`` — kicks off a new run.
- ``Orchestrator.resume_run(run_id)`` — resumes from the last
  successfully-completed phase (idempotent).
- ``Orchestrator.cancel_run(run_id)`` — marks the run as cancelled.

The orchestrator emits ``PipelineEvent``s (from
``seharness.pipeline.vertical_slice``) for every phase boundary, so the
existing E2E test contract is preserved.

Production entry points (``/feature``, ``seharness run``, Telegram) all
go through ``ControllerApplicationService.feature_request`` which
delegates to ``Orchestrator.start_run``.
"""

from seharness.orchestrator.orchestrator import (
    OrchestrationService,
    Orchestrator,
)
from seharness.orchestrator.phases import PHASE_SEQUENCE
from seharness.orchestrator.runner import (
    CommandResult,
    LocalCommandRunner,
    StubRunner,
)
from seharness.orchestrator.types import (
    OrchestratorConfig,
    PhaseName,
    PhaseOutcome,
    RunContext,
    RunId,
)

__all__ = [
    "PHASE_SEQUENCE",
    "CommandResult",
    "LocalCommandRunner",
    "OrchestrationService",
    "Orchestrator",
    "OrchestratorConfig",
    "PhaseName",
    "PhaseOutcome",
    "RunContext",
    "RunId",
    "StubRunner",
]
