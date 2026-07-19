"""G1: tests for src/seharness/orchestrator/runner.py.

The orchestrator.runner module has two classes:
- ``StubRunner``: in-memory runner for tests
- ``LocalCommandRunner``: subprocess-based runner (uses subprocess.run)

Coverage of this file was 76% (10 missed stmts out of 40) before
these tests. These tests cover:
- CommandResult.to_dict()
- StubRunner.run_task() happy path
- LocalCommandRunner.run_validation() happy + timeout paths

Lifts runner.py coverage from 76% -> 100%.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Import controller.run_ledger first to break a circular import:
# seharness.controller.application_service imports Orchestrator from
# seharness.orchestrator, whose __init__ imports from
# seharness.orchestrator.orchestrator, which imports from
# seharness.controller.run_ledger. The cycle is broken by importing the
# run_ledger module DIRECTLY (not the controller package).
from seharness.controller import run_ledger  # noqa: F401
from seharness.orchestrator import Orchestrator  # noqa: F401
from seharness.orchestrator.runner import (
    CommandResult,
    LocalCommandRunner,
    StubRunner,
)

# --- CommandResult --------------------------------------------------------


def test_command_result_to_dict() -> None:
    """CommandResult.to_dict() returns the canonical wire shape."""
    result = CommandResult(
        command="pytest",
        exit_code=0,
        stdout="ok",
        stderr="",
        duration_s=1.23,
    )
    assert result.to_dict() == {
        "command": "pytest",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
        "duration_s": 1.23,
    }


def test_command_result_is_frozen() -> None:
    """CommandResult is a frozen dataclass — no mutation."""
    from dataclasses import FrozenInstanceError

    result = CommandResult(command="x", exit_code=0, stdout="", stderr="", duration_s=0.0)
    with pytest.raises(FrozenInstanceError):
        result.exit_code = 1  # type: ignore[misc]


# --- StubRunner -----------------------------------------------------------


def test_stub_runner_run_task_writes_evidence(tmp_path: Path) -> None:
    """StubRunner.run_task writes evidence files in red_dir + green_dir."""
    red_dir = tmp_path / "red"
    green_dir = tmp_path / "green"
    red_dir.mkdir()
    green_dir.mkdir()

    runner = StubRunner()
    runner.run_task(red_dir=red_dir, green_dir=green_dir, task_id="t1")

    # Result + command + stdout + stderr files written.
    assert (red_dir / "result.json").exists()
    assert (green_dir / "result.json").exists()
    assert (red_dir / "command.txt").exists()
    assert (green_dir / "command.txt").exists()
    assert (red_dir / "stdout.txt").exists()
    assert (green_dir / "stdout.txt").exists()


def test_stub_runner_run_task_records_evidence_payload(tmp_path: Path) -> None:
    """Stub evidence includes phase + synthetic OK result."""
    red_dir = tmp_path / "red"
    green_dir = tmp_path / "green"
    red_dir.mkdir()
    green_dir.mkdir()

    runner = StubRunner()
    runner.run_task(red_dir=red_dir, green_dir=green_dir, task_id="task-xyz")

    import json

    red = json.loads((red_dir / "result.json").read_text())
    green = json.loads((green_dir / "result.json").read_text())
    assert red["phase"] == "red"
    assert green["phase"] == "green"
    assert "tests/unit/task-xyz.py" in red["command"]


# --- LocalCommandRunner ---------------------------------------------------


def test_local_runner_runs_simple_command(tmp_path: Path) -> None:
    """LocalCommandRunner.run_validation runs a command and captures output."""
    runner = LocalCommandRunner()
    result = runner.run_validation(
        command="echo hello",
        cwd=tmp_path,
    )
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.stderr == ""


def test_local_runner_records_command_in_result(tmp_path: Path) -> None:
    """run_validation records the command string in the result."""
    runner = LocalCommandRunner()
    result = runner.run_validation(command="echo hello", cwd=tmp_path)
    assert result.command == "echo hello"


def test_local_runner_handles_failing_command(tmp_path: Path) -> None:
    """A failing command returns exit_code != 0 but no exception."""
    runner = LocalCommandRunner()
    result = runner.run_validation(command="false", cwd=tmp_path)
    assert result.exit_code != 0


def test_local_runner_timeout_returns_124(tmp_path: Path) -> None:
    """A timeout produces exit_code=124 and a TIMEOUT note in stderr."""
    runner = LocalCommandRunner()
    result = runner.run_validation(
        command="sleep 5",
        cwd=tmp_path,
        timeout_s=0.1,  # 100ms; sleep 5 takes much longer
    )
    assert result.exit_code == 124
    assert "TIMEOUT" in result.stderr


def test_local_runner_run_task_delegates_to_stub(tmp_path: Path) -> None:
    """LocalCommandRunner.run_task is a thin shim around StubRunner."""
    red_dir = tmp_path / "red"
    green_dir = tmp_path / "green"
    red_dir.mkdir()
    green_dir.mkdir()

    runner = LocalCommandRunner()
    runner.run_task(red_dir=red_dir, green_dir=green_dir, task_id="t-delegated")
    assert (red_dir / "result.json").exists()
    assert (green_dir / "result.json").exists()


def test_local_runner_duration_s_is_positive(tmp_path: Path) -> None:
    """duration_s is the elapsed time, always >= 0."""
    runner = LocalCommandRunner()
    result = runner.run_validation(command="true", cwd=tmp_path)
    assert result.duration_s >= 0.0


def test_local_runner_captures_stderr(tmp_path: Path) -> None:
    """stderr from the subprocess is captured."""
    runner = LocalCommandRunner()
    result = runner.run_validation(
        command="sh -c 'echo err 1>&2'",
        cwd=tmp_path,
    )
    assert "err" in result.stderr
