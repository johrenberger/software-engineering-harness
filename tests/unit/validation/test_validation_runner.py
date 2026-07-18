"""RED \u2014 Slice 7 validation runner boundary.

Per SPEC \u00a7"Validation runner" and slice 7 GREEN deliverables, the
runner is the public boundary used by slice 9 to invoke test
commands. It returns a ``CommandResult`` (raw outcome) which the
classifier then turns into a ``NormalizedFailure``.

For slice 7 the runner interface is:
- ``ValidationRunner.run(command: str) -> CommandResult``

The default implementation is ``SubprocessRunner`` which actually
invokes the command via ``subprocess.run``. Tests inject a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestValidationRunnerProtocol:
    """The runner protocol is documented and stable."""

    def test_runner_has_run_method(self) -> None:
        from seharness.validation.runner import ValidationRunner  # noqa: PLC0415

        assert hasattr(ValidationRunner, "run")


class TestSubprocessRunner:
    """``SubprocessRunner`` invokes ``subprocess.run`` and returns a
    ``CommandResult``."""

    def test_subprocess_runner_invokes_command(self, tmp_path: Path) -> None:
        from seharness.validation.runner import (  # noqa: PLC0415
            CommandResult,
            SubprocessRunner,
        )

        runner = SubprocessRunner()
        result = runner.run(
            "true",  # `true` exits 0 on linux/macos
        )
        assert isinstance(result, CommandResult)
        assert result.exit_code == 0

    def test_subprocess_runner_captures_exit_code(self) -> None:
        from seharness.validation.runner import (  # noqa: PLC0415
            CommandResult,
            SubprocessRunner,
        )

        runner = SubprocessRunner()
        result = runner.run(
            "false",  # `false` exits 1 on linux/macos
        )
        assert isinstance(result, CommandResult)
        assert result.exit_code != 0

    def test_subprocess_runner_measures_duration(self) -> None:
        from seharness.validation.runner import (  # noqa: PLC0415
            SubprocessRunner,
        )

        runner = SubprocessRunner()
        result = runner.run("true")
        assert result.duration_s >= 0.0


class TestCommandResultValidation:
    """``CommandResult`` rejects impossible values."""

    def test_command_result_rejects_negative_duration(self) -> None:
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        with pytest.raises(Exception):  # noqa: B017
            CommandResult(
                command="t",
                exit_code=1,
                stdout="",
                stderr="",
                duration_s=-1.0,
            )

    def test_command_result_accepts_zero_duration(self) -> None:
        """Boundary value: ``ge=0`` allows 0."""
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        r = CommandResult(
            command="t",
            exit_code=1,
            stdout="",
            stderr="",
            duration_s=0,
        )
        assert r.duration_s == 0


class TestFailureKindDeterminism:
    """``FailureKind`` is a StrEnum (deterministic string values)."""

    def test_failure_kind_round_trip(self) -> None:
        from seharness.validation.runner import FailureKind  # noqa: PLC0415

        for k in FailureKind:
            assert FailureKind(k.value) == k
            assert str(k) == k.value
            assert k.name in repr(k)
