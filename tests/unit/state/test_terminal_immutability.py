"""RED phase: terminal-state immutability.

Per the harness spec:
- terminal states must be immutable
- completed phase artifacts must not be regenerated unless explicitly invalidated
- invalid transitions fail

This module asserts that once a run has reached a terminal phase
(COMPLETED / FAILED / BLOCKED), the state machine refuses to
transition any further -- and ``is_terminal`` reports it correctly.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from seharness.domain.enums import PhaseName, RunStatus
from seharness.state_machine import (
    TERMINAL_PHASES,
    InvalidTransitionError,
    WorkflowState,
)


class TestTerminalPhasesSet:
    def test_terminal_phases_set_exposes_completed_failed_blocked(self) -> None:
        assert (
            frozenset({PhaseName.COMPLETED, PhaseName.FAILED, PhaseName.BLOCKED}) == TERMINAL_PHASES
        )

    def test_normal_phases_are_not_terminal(self) -> None:
        for p in (
            PhaseName.INTAKE,
            PhaseName.DISCOVERY,
            PhaseName.SPECIFICATION,
            PhaseName.PLANNING,
            PhaseName.EXECUTION,
            PhaseName.VALIDATION,
        ):
            assert p not in TERMINAL_PHASES


class TestIsTerminalProperty:
    def test_completed_is_terminal(self) -> None:
        s = WorkflowState(
            run_id="r1", current_phase=PhaseName.COMPLETED, run_status=RunStatus.COMPLETED
        )
        assert s.is_terminal is True

    def test_failed_is_terminal(self) -> None:
        s = WorkflowState(run_id="r1", current_phase=PhaseName.FAILED, run_status=RunStatus.FAILED)
        assert s.is_terminal is True

    def test_blocked_is_terminal(self) -> None:
        s = WorkflowState(
            run_id="r1", current_phase=PhaseName.BLOCKED, run_status=RunStatus.BLOCKED
        )
        assert s.is_terminal is True

    @pytest.mark.parametrize(
        "phase",
        [
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
        ],
    )
    def test_normal_phases_are_not_terminal(self, phase: PhaseName) -> None:
        s = WorkflowState(run_id="r1", current_phase=phase, run_status=RunStatus.RUNNING)
        assert s.is_terminal is False


class TestTerminalTransitionRefusal:
    """Once terminal, the state machine refuses further transitions."""

    @pytest.mark.parametrize(
        "target",
        [
            PhaseName.EXECUTION,
            PhaseName.PLANNING,
            PhaseName.REMEDIATION,
            PhaseName.DELIVERY,
            PhaseName.INTAKE,
        ],
    )
    def test_completed_run_refuses_any_transition(self, target: PhaseName) -> None:
        s = WorkflowState(
            run_id="r1", current_phase=PhaseName.COMPLETED, run_status=RunStatus.COMPLETED
        )
        with pytest.raises(Exception) as ei:
            s.transition_to(target)
        # Must be the domain-specific error.
        assert isinstance(ei.value, InvalidTransitionError)

    @pytest.mark.parametrize(
        "target",
        [PhaseName.EXECUTION, PhaseName.REVIEW, PhaseName.PLANNING],
    )
    def test_failed_run_refuses_recovery_transition(self, target: PhaseName) -> None:
        s = WorkflowState(run_id="r1", current_phase=PhaseName.FAILED, run_status=RunStatus.FAILED)
        with pytest.raises(InvalidTransitionError):
            s.transition_to(target)

    def test_dataclass_is_frozen(self) -> None:
        """The ``frozen=True`` dataclass decorator is required: terminal
        immutability is enforced both at the type level and at the
        transition layer.
        """
        s = WorkflowState(run_id="r1", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING)
        with pytest.raises(FrozenInstanceError):
            s.current_phase = PhaseName.EXECUTION  # type: ignore[misc]


class TestTerminalArtifactsNotRegenerated:
    """The state machine does not, itself, write artifacts, but it must
    be safe for the artifact store to delegate the ``do not regenerate
    completed artifacts unless invalidated`` rule. The state machine
    guarantees this by being a value type: there is no
    'regenerate-on-transition' method."""

    def test_no_method_regenerates_state(self) -> None:
        s = WorkflowState(run_id="r1", current_phase=PhaseName.INTAKE, run_status=RunStatus.RUNNING)
        # No method that returns a new state without explicit transition.
        for name in dir(s):
            if name.startswith("__"):
                continue
            attr = getattr(s, name)
            if callable(attr) and name != "transition_to":
                # Methods that intentionally exist: is_terminal.
                assert name in {"is_terminal", "_derive_run_status"}, (
                    f"unexpected callable {name!r} on WorkflowState"
                )
