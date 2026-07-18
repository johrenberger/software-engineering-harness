"""Workflow state machine.

This module owns:

- ``ALLOWED_TRANSITIONS`` — the canonical transition table mandated by
  the harness spec. Phase-to-phase routing lives here, not in any phase
  module.
- ``InvalidTransitionError`` — domain exception raised when a transition
  is attempted that does not appear in the table.
- ``WorkflowState`` — an immutable value object representing one snapshot
  of a run.

Persistence is intentionally separate: this module has no I/O and no
filesystem dependency. The artifact store (slice 2 next module) loads
and persists ``WorkflowState`` via its own Pydantic model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from seharness.domain.enums import PhaseName, RunStatus


class InvalidTransitionError(ValueError):
    """Raised when a phase transition is attempted that the spec forbids.

    The error message includes both the source and target phase names so
    log readers can diagnose the offending transition without a debugger.
    """

    def __init__(self, source: PhaseName, target: PhaseName) -> None:
        self.source = source
        self.target = target
        super().__init__(f"invalid transition: {source.value!r} -> {target.value!r}")


# Canonical transition table. Mirrors the spec exactly; do not edit
# without also updating the documentation in §7 of the harness scaffold
# instructions.
ALLOWED_TRANSITIONS: dict[PhaseName, frozenset[PhaseName]] = {
    PhaseName.INTAKE: frozenset({PhaseName.DISCOVERY, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.DISCOVERY: frozenset({PhaseName.SPECIFICATION, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.SPECIFICATION: frozenset({PhaseName.IMPACT, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.IMPACT: frozenset({PhaseName.PLANNING, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.PLANNING: frozenset({PhaseName.EXECUTION, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.EXECUTION: frozenset(
        {
            PhaseName.VALIDATION,
            PhaseName.PLANNING,
            PhaseName.BLOCKED,
            PhaseName.FAILED,
        }
    ),
    PhaseName.VALIDATION: frozenset(
        {
            PhaseName.REVIEW,
            PhaseName.REMEDIATION,
            PhaseName.PLANNING,
            PhaseName.BLOCKED,
            PhaseName.FAILED,
        }
    ),
    PhaseName.REMEDIATION: frozenset(
        {
            PhaseName.VALIDATION,
            PhaseName.REVIEW,
            PhaseName.PLANNING,
            PhaseName.BLOCKED,
            PhaseName.FAILED,
        }
    ),
    PhaseName.REVIEW: frozenset(
        {
            PhaseName.DELIVERY,
            PhaseName.REMEDIATION,
            PhaseName.BLOCKED,
            PhaseName.FAILED,
        }
    ),
    PhaseName.DELIVERY: frozenset({PhaseName.CI_MONITORING, PhaseName.BLOCKED, PhaseName.FAILED}),
    PhaseName.CI_MONITORING: frozenset(
        {
            PhaseName.COMPLETED,
            PhaseName.REMEDIATION,
            PhaseName.BLOCKED,
            PhaseName.FAILED,
        }
    ),
}

# Terminal phases. Transitions out of these are forbidden; transitions
# into them are allowed only as listed above. Exposed publicly so the
# artifact store (this slice) and run-resumption service (slice 9) can
# both short-circuit without depending on is_terminal internals.
TERMINAL_PHASES: frozenset[PhaseName] = frozenset(
    {PhaseName.COMPLETED, PhaseName.FAILED, PhaseName.BLOCKED}
)


@dataclass(frozen=True)
class WorkflowState:
    """Immutable snapshot of a run.

    The frozen dataclass ensures that every transition produces a new
    value rather than mutating the prior one, which is what makes the
    resumable repository story work: the artifact store always writes
    a new file rather than patching the existing one.
    """

    run_id: str
    current_phase: PhaseName
    run_status: RunStatus
    # Retry counters; persistent across transitions.
    task_retries: int = 0
    repair_retries: int = 0
    # Audit trail of phase transitions within this run.
    history: tuple[tuple[PhaseName, PhaseName], ...] = ()
    # last-changed timestamp is informational; the on-disk artifact store
    # records its own clock at write time.
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_terminal(self) -> bool:
        """True when the run has reached a terminal phase.

        A terminal run is one whose ``current_phase`` is COMPLETED,
        FAILED, or BLOCKED. From a terminal phase the state machine
        refuses any further transition; the artifact store likewise
        treats the run as immutable on disk.
        """
        return self.current_phase in TERMINAL_PHASES

    def transition_to(self, target: PhaseName) -> WorkflowState:
        """Return a new WorkflowState at ``target``.

        Raises ``InvalidTransitionError`` if the transition is not in
        ``ALLOWED_TRANSITIONS``.
        """
        # Terminal phases appear in the table only as targets, never as
        # sources. The first guard makes the error clearer for callers
        # that have already moved a run to COMPLETED/FAILED/BLOCKED and
        # try to drive it further.
        if self.current_phase in TERMINAL_PHASES:
            raise InvalidTransitionError(self.current_phase, target)
        if target not in ALLOWED_TRANSITIONS[self.current_phase]:
            raise InvalidTransitionError(self.current_phase, target)

        new_history = (*self.history, (self.current_phase, target))
        new_status = self._derive_run_status(target)
        return WorkflowState(
            run_id=self.run_id,
            current_phase=target,
            run_status=new_status,
            task_retries=self.task_retries,
            repair_retries=self.repair_retries,
            history=new_history,
            updated_at=datetime.now(UTC),
        )

    @staticmethod
    def _derive_run_status(target: PhaseName) -> RunStatus:
        """Map a target phase to a high-level run status."""
        if target == PhaseName.COMPLETED:
            return RunStatus.COMPLETED
        if target == PhaseName.FAILED:
            return RunStatus.FAILED
        if target == PhaseName.BLOCKED:
            return RunStatus.BLOCKED
        return RunStatus.RUNNING
