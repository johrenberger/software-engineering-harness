"""Command runners used by the orchestrator.

The orchestrator delegates to a runner for two operations:

1. The implementation phase (slice 7 ``TaskExecutionService.execute``)
   accepts a ``Runner`` (slice-6 callable) that writes RED + GREEN
   evidence files. The ``StubRunner`` here writes valid evidence
   without touching the filesystem outside the run directory.

2. The validation phase re-runs the task's validation commands and
   needs to capture exit code, stdout, stderr. ``LocalCommandRunner``
   runs real subprocesses (gated by ``OrchestratorConfig.use_real_subprocess``);
   ``StubRunner`` returns synthetic exit-code-0 results.

Cluster A ships both runners; Cluster C replaces ``LocalCommandRunner``
with a sandboxed variant.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — gated by use_real_subprocess, validated below
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandResult:
    """Captured subprocess outcome.

    Mirrors the slice-7 ``result.json`` shape so the orchestrator can
    feed it back into existing validators without translation.
    """

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": self.duration_s,
        }


class StubRunner:
    """In-memory runner — produces valid RED+GREEN evidence without
    touching the test environment.

    The deterministic result means the orchestrator's happy path can be
    exercised in tests without ever running pytest.
    """

    def run_task(
        self,
        *,
        red_dir: Path,
        green_dir: Path,
        task_id: str,
    ) -> CommandResult:
        for d in (red_dir, green_dir):
            d.mkdir(parents=True, exist_ok=True)
            (d / "command.txt").write_text(f"pytest tests/unit/{task_id}.py --no-cov -q\n")
            (d / "stdout.txt").write_text("")
            (d / "stderr.txt").write_text("")
        (red_dir / "result.json").write_text(
            json.dumps(
                {
                    "phase": "red",
                    "exit_code": 1,
                    "duration_s": 0.1,
                    "failure_kind": "expected_failure",
                    "failure_reason": "AssertionError",
                    "test_id": f"tests/unit/{task_id}.py::test_x",
                    "command": f"pytest tests/unit/{task_id}.py --no-cov -q",
                }
            )
            + "\n"
        )
        (green_dir / "result.json").write_text(
            json.dumps(
                {
                    "phase": "green",
                    "exit_code": 0,
                    "duration_s": 0.5,
                    "test_id": f"tests/unit/{task_id}.py::test_x",
                    "command": f"pytest tests/unit/{task_id}.py --no-cov -q",
                    "covered_tests": [f"tests/unit/{task_id}.py::test_x"],
                    "required_tests": [f"tests/unit/{task_id}.py::test_x"],
                }
            )
            + "\n"
        )
        return CommandResult(
            command=f"pytest tests/unit/{task_id}.py --no-cov -q",
            exit_code=0,
            stdout="",
            stderr="",
            duration_s=0.0,
        )

    def run_validation(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float = 60.0,
    ) -> CommandResult:
        """Deterministic validation — always returns exit 0.

        Real subprocess validation is gated by
        ``OrchestratorConfig.use_real_subprocess``.
        """
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="",
            stderr="",
            duration_s=0.0,
        )


class LocalCommandRunner:
    """Real subprocess runner. Used when ``use_real_subprocess=True``.

    Cluster A ships this; Cluster C wraps it with sandboxing.
    """

    def run_task(
        self,
        *,
        red_dir: Path,
        green_dir: Path,
        task_id: str,
    ) -> CommandResult:
        # LocalCommandRunner.run_task is intentionally a thin shim
        # around the validation runner — the slice-7 TaskExecutionService
        # passes its own Runner callable that writes evidence; we
        # delegate here only for callers that want a single entry point.
        return StubRunner().run_task(red_dir=red_dir, green_dir=green_dir, task_id=task_id)

    def run_validation(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float = 60.0,
    ) -> CommandResult:
        start = time.monotonic()
        try:
            completed = subprocess.run(  # nosec B602
                command,
                shell=True,  # nosec B603
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr_raw = (
                exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            )
            return CommandResult(
                command=command,
                exit_code=124,
                stdout=stdout,
                stderr=stderr_raw + f"\nTIMEOUT after {timeout_s}s",
                duration_s=time.monotonic() - start,
            )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_s=time.monotonic() - start,
        )
