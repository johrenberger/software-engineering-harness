"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 4.

'Failed local validation blocks PR creation':
- LocalValidationGate MUST run ruff, mypy, bandit, pytest (configurable).
- If any gate fails, the gate returns a failing result.
- DeliveryService MUST NOT call PR client when gate fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.delivery.gate import (
    GateFailureError,
    GateResult,
    GateRunner,
    LocalValidationGate,
)
from seharness.delivery.pr import PullRequestClient


class _AlwaysPassing(GateRunner):
    def run(self, repo_root: Path) -> GateResult:
        return GateResult(gate_id="test", passed=True, output="")


class _AlwaysFailing(GateRunner):
    def run(self, repo_root: Path) -> GateResult:
        return GateResult(gate_id="test", passed=False, output="something broke")


def test_gate_result_dataclass_passes() -> None:
    r = GateResult(gate_id="ruff", passed=True, output="")
    assert r.passed is True


def test_gate_result_dataclass_fails() -> None:
    r = GateResult(gate_id="ruff", passed=False, output="oops")
    assert r.passed is False


def test_local_validation_gate_runs_all_runners() -> None:
    """All configured runners MUST be executed."""
    runner_calls: list[Path] = []

    class _RecordingRunner(GateRunner):
        def run(self, repo_root: Path) -> GateResult:
            runner_calls.append(repo_root)
            return GateResult(gate_id="x", passed=True, output="")

    gate = LocalValidationGate(runners=(_RecordingRunner(), _RecordingRunner()))
    result = gate.run(Path("/tmp"))
    assert len(runner_calls) == 2
    assert result.passed is True


def test_local_validation_gate_fails_when_any_runner_fails() -> None:
    gate = LocalValidationGate(runners=(_AlwaysPassing(), _AlwaysFailing()))
    result = gate.run(Path("/tmp"))
    assert result.passed is False


def test_local_validation_gate_short_circuits_on_first_failure() -> None:
    """Stop running gates once one fails (avoid wasted work)."""
    second_called = False

    class _ShortCircuitCheck(GateRunner):
        def run(self, repo_root: Path) -> GateResult:
            nonlocal second_called
            second_called = True
            return GateResult(gate_id="x", passed=True, output="")

    gate = LocalValidationGate(runners=(_AlwaysFailing(), _ShortCircuitCheck()))
    gate.run(Path("/tmp"))
    assert second_called is False


def test_local_validation_gate_raises_on_failure() -> None:
    """A failing gate raises GateFailureError when raise_on_failure=True."""
    gate = LocalValidationGate(runners=(_AlwaysFailing(),), raise_on_failure=True)
    with pytest.raises(GateFailureError):
        gate.run(Path("/tmp"))


def test_local_validation_gate_no_runners_returns_passing() -> None:
    """Empty runners list trivially passes (degenerate case)."""
    gate = LocalValidationGate(runners=())
    result = gate.run(Path("/tmp"))
    assert result.passed is True


def test_pull_request_client_is_protocol() -> None:
    """PullRequestClient is a Protocol — controller wires concrete impl."""
    assert hasattr(PullRequestClient, "create")
    assert hasattr(PullRequestClient, "get")


def test_gate_failure_error_includes_failed_gate_id() -> None:
    gate = LocalValidationGate(runners=(_AlwaysFailing(),), raise_on_failure=True)
    try:
        gate.run(Path("/tmp"))
    except GateFailureError as exc:
        assert exc.failed_gate_ids  # non-empty
        assert "test" in exc.failed_gate_ids
    else:
        pytest.fail("expected GateFailureError")
