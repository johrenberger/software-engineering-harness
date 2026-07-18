"""RED phase: phase enums and invalid transition rejection.

Every downstream slice of the workflow (slice 3+ will read/write
state.py, slice 9 will resume from persisted state, etc.) is going
to import from this surface. Strict TDD: write the test that fails
when the surface doesn't exist yet, then implement only what makes
it pass.
"""

from __future__ import annotations

import pytest

from seharness.domain.enums import PhaseName, RunStatus
from seharness.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    WorkflowState,
)


# ---------------------------------------------------------------------
# 1. The RunStatus and PhaseName StrEnums are exported and exhaustive.
# ---------------------------------------------------------------------
class TestRunStatusEnum:
    """`RunStatus` covers the five terminal/observable states."""

    def test_known_members_present(self) -> None:
        members = {m.value for m in RunStatus}
        assert {"created", "running", "blocked", "failed", "completed"} <= members

    def test_is_string_enum(self) -> None:
        # Members are usable in JSON / dict keys / set lookups.
        assert RunStatus.RUNNING == "running"
        assert hash(RunStatus.RUNNING) == hash("running")


class TestPhaseNameEnum:
    """`PhaseName` covers every workflow phase plus terminal markers."""

    def test_known_phase_members_present(self) -> None:
        members = {m.value for m in PhaseName}
        expected = {
            "intake",
            "discovery",
            "specification",
            "impact",
            "planning",
            "execution",
            "validation",
            "remediation",
            "review",
            "delivery",
            "ci_monitoring",
            "completed",
            "blocked",
            "failed",
        }
        assert expected <= members

    def test_is_string_enum(self) -> None:
        assert PhaseName.EXECUTION == "execution"


# ---------------------------------------------------------------------
# 2. ALLOWED_TRANSITIONS table is exported and reflects the spec.
# ---------------------------------------------------------------------
class TestAllowedTransitionsTable:
    """The doc-mandated transition table must be present in code."""

    def test_intake_can_transition_to_discovery_blocked_or_failed(self) -> None:
        assert ALLOWED_TRANSITIONS[PhaseName.INTAKE] == frozenset(
            {PhaseName.DISCOVERY, PhaseName.BLOCKED, PhaseName.FAILED}
        )

    def test_discovery_can_transition_to_specification_blocked_or_failed(self) -> None:
        assert ALLOWED_TRANSITIONS[PhaseName.DISCOVERY] == frozenset(
            {PhaseName.SPECIFICATION, PhaseName.BLOCKED, PhaseName.FAILED}
        )

    def test_specification_can_transition_to_impact_blocked_or_failed(self) -> None:
        assert ALLOWED_TRANSITIONS[PhaseName.SPECIFICATION] == frozenset(
            {PhaseName.IMPACT, PhaseName.BLOCKED, PhaseName.FAILED}
        )

    def test_planning_can_reenter_on_validation_failure(self) -> None:
        # Validation -> Planning is part of the spec; let's pin it.
        assert PhaseName.PLANNING in ALLOWED_TRANSITIONS[PhaseName.VALIDATION]

    def test_known_table_covers_every_normal_phase(self) -> None:
        normal_phases = {
            PhaseName.INTAKE,
            PhaseName.DISCOVERY,
            PhaseName.SPECIFICATION,
            PhaseName.IMPACT,
            PhaseName.PLANNING,
            PhaseName.EXECUTION,
            PhaseName.VALIDATION,
            PhaseName.REMEDIATION,
            PhaseName.REVIEW,
            PhaseName.DELIVERY,
            PhaseName.CI_MONITORING,
        }
        assert normal_phases <= ALLOWED_TRANSITIONS.keys()


# ---------------------------------------------------------------------
# 3. Invalid transitions raise InvalidTransitionError.
# ---------------------------------------------------------------------
class TestInvalidTransitionRejection:
    @pytest.mark.parametrize(
        "src, dst",
        [
            (PhaseName.INTAKE, PhaseName.EXECUTION),
            (PhaseName.INTAKE, PhaseName.COMPLETED),
            (PhaseName.DISCOVERY, PhaseName.EXECUTION),
            (PhaseName.PLANNING, PhaseName.REVIEW),
            (PhaseName.EXECUTION, PhaseName.INTAKE),
            (PhaseName.DELIVERY, PhaseName.EXECUTION),
            (PhaseName.CI_MONITORING, PhaseName.INTAKE),
            # Cannot move from a terminal back to a working phase.
            (PhaseName.COMPLETED, PhaseName.EXECUTION),
            (PhaseName.FAILED, PhaseName.PLANNING),
        ],
    )
    def test_invalid_transition_raises(self, src: PhaseName, dst: PhaseName) -> None:
        state = WorkflowState(run_id="r-test", current_phase=src, run_status=RunStatus.RUNNING)
        with pytest.raises(InvalidTransitionError) as ei:
            state.transition_to(dst)
        # The exception must mention both source and target phases.
        msg = str(ei.value)
        assert src.value in msg, f"error must mention source phase {src.value}; got: {msg!r}"
        assert dst.value in msg, f"error must mention target phase {dst.value}; got: {msg!r}"

    def test_valid_transition_returns_new_state(self) -> None:
        state = WorkflowState(
            run_id="r-test", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING
        )
        next_state = state.transition_to(PhaseName.DISCOVERY)
        assert next_state.current_phase == PhaseName.DISCOVERY
        assert next_state.run_id == "r-test"
