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

Cluster E, story E4b: every ``run_*`` method accepts an optional
``cancel: CancellationToken | None = None``. When the token fires,
``LocalCommandRunner.run_validation`` sends SIGTERM (then SIGKILL
after a grace window) to the subprocess via the cluster-E4a
``CancellationWatcher`` (see ``seharness.sandbox.cancellation``).
``StubRunner`` ignores cancellation entirely — its work is
synchronous and instant, so cancel is moot.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal as _signal
import subprocess  # nosec B404 — gated by use_real_subprocess, validated below
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seharness.sandbox.cancellation import CancellationToken


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
        cancel: CancellationToken | None = None,
        pending_changes: Sequence[str] | None = None,
    ) -> CommandResult:
        # ``StubRunner`` is synchronous and instant; cancellation is a
        # no-op. The parameter is accepted (and ignored) so callers
        # (the orchestrator) can pass the same cancel token to every
        # runner method without branching on the concrete runner type.
        # ``pending_changes`` is accepted for parity with
        # :class:`LLMDrivenTaskRunner`; the deterministic stub does
        # not apply model-produced patches.
        _ = pending_changes
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
        cancel: CancellationToken | None = None,
    ) -> CommandResult:
        """Deterministic validation — always returns exit 0.

        Real subprocess validation is gated by
        ``OrchestratorConfig.use_real_subprocess``. The ``cancel``
        parameter is accepted (and ignored) so callers can pass the
        same token to every runner method without branching on the
        concrete runner type.
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

    Cluster E, story E4b: ``run_validation`` accepts a
    ``CancellationToken``. When provided, the runner uses
    ``subprocess.Popen`` instead of ``subprocess.run`` so the
    watcher thread (SIGTERM → grace → SIGKILL) can actually reach
    the child. ``run_task`` is still a thin shim over ``StubRunner``
    — the slice-7 TaskExecutionService drives the heavy lifting.
    """

    # SIGTERM (POSIX) / taskkill /T (Windows) — used to politely ask
    # the child to terminate. The watcher handles SIGKILL escalation.
    _CANCEL_GRACE_SECONDS: float = 5.0

    def run_task(
        self,
        *,
        red_dir: Path,
        green_dir: Path,
        task_id: str,
        cancel: CancellationToken | None = None,
        pending_changes: Sequence[str] | None = None,
    ) -> CommandResult:
        # LocalCommandRunner.run_task is intentionally a thin shim
        # around the validation runner — the slice-7 TaskExecutionService
        # passes its own Runner callable that writes evidence; we
        # delegate here only for callers that want a single entry point.
        # ``pending_changes`` is forwarded to the underlying stub so
        # the signature matches :class:`LLMDrivenTaskRunner`'s.
        return StubRunner().run_task(
            red_dir=red_dir,
            green_dir=green_dir,
            task_id=task_id,
            pending_changes=pending_changes,
        )

    def run_validation(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float = 60.0,
        cancel: CancellationToken | None = None,
    ) -> CommandResult:
        start = time.monotonic()
        # When no cancel token is provided we keep the fast path:
        # ``subprocess.run`` with a timeout, no watcher thread. The
        # token path needs ``Popen`` + a watcher, which is more
        # expensive but is the only way cancellation can actually
        # reach the child before the timeout fires.
        if cancel is None:
            return self._run_validation_simple(
                command=command, cwd=cwd, timeout_s=timeout_s, start=start
            )
        return self._run_validation_cancellable(
            command=command, cwd=cwd, timeout_s=timeout_s, cancel=cancel, start=start
        )

    def _run_validation_simple(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float,
        start: float,
    ) -> CommandResult:
        """Original ``subprocess.run(timeout=)`` fast path (no cancel)."""
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

    def _run_validation_cancellable(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float,
        cancel: CancellationToken,
        start: float,
    ) -> CommandResult:
        """``Popen`` + watcher path. Cancel can interrupt before timeout.

        POSIX caveat: with ``shell=True`` we get a shell parent and
        a Python grandchild. ``proc.terminate()`` only kills the
        shell; the grandchild keeps the stdout/stderr pipes open
        and ``communicate()`` hangs forever. We avoid this by
        starting a new session and terminating the entire process
        group on cancellation.
        """
        try:
            # ``start_new_session=True`` puts the child into its own
            # process group so SIGTERM/SIGKILL can reach the whole
            # subtree (shell + grandchild).
            proc = subprocess.Popen(  # nosec B602
                command,
                shell=True,  # nosec B603
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return CommandResult(
                command=command,
                exit_code=127,
                stdout="",
                stderr=f"failed to spawn subprocess: {exc}",
                duration_s=time.monotonic() - start,
            )

        # Wait for the child to exit, but wake up promptly when the
        # token fires. We poll ``proc.poll`` in short slices and
        # check the token + the overall timeout each slice.
        exit_code = self._wait_with_group_cancel(proc=proc, cancel=cancel, timeout_s=timeout_s)
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
        except (
            subprocess.TimeoutExpired
        ):  # pragma: no cover  # defensive: belt-and-braces if pipes wedge after SIGKILL
            # Belt-and-braces: even after SIGKILL on the group, the
            # pipes may be wedged. Force-reap and move on.
            self._kill_process_group(proc)  # pragma: no cover
            try:  # pragma: no cover  # nested safety net
                stdout, stderr = proc.communicate(timeout=1.0)  # pragma: no cover
            except subprocess.TimeoutExpired:  # pragma: no cover
                stdout, stderr = "", ""  # pragma: no cover

        if cancel.is_cancelled() and exit_code not in (0,):
            # Cancellation fired and the process was reaped by the
            # watcher. Use a sentinel exit code (130 = 128+SIGTERM,
            # matching sh convention).
            return CommandResult(
                command=command,
                exit_code=130,
                stdout=stdout or "",
                stderr=(stderr or "") + "\n[cancelled by orchestrator]",
                duration_s=time.monotonic() - start,
            )
        return CommandResult(
            command=command,
            exit_code=exit_code if exit_code is not None else 124,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_s=time.monotonic() - start,
        )

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen[str]) -> None:
        """Send SIGKILL to the entire process group of ``proc``.

        Used as a fallback when ``communicate()`` itself hangs even
        after the watcher fired. POSIX-only; on other platforms we
        fall back to terminating the proc directly (the watcher
        thread will already have done the escalation).
        """
        try:
            os.killpg(proc.pid, _signal.SIGKILL)
        except (OSError, ProcessLookupError, AttributeError):
            # Windows / process already gone / no ``pgid`` available.
            with contextlib.suppress(Exception):
                proc.kill()

    @staticmethod
    def _wait_with_group_cancel(
        *,
        proc: subprocess.Popen[str],
        cancel: CancellationToken,
        timeout_s: float,
        poll_interval: float = 0.05,
    ) -> int | None:
        """Wait for ``proc``, waking early on cancel/timeout.

        On cancellation we send SIGTERM to the entire process
        group, wait up to ``grace_seconds + safety`` for the
        subprocess to exit, then SIGKILL the group if it's still
        alive. Mirrors what ``CancellationWatcher`` does for
        single-process targets but operates at the group level
        (necessary because ``shell=True`` spawns a shell + child).
        """
        deadline = time.monotonic() + timeout_s
        grace = 5.0  # matches _CANCEL_GRACE_SECONDS
        while True:
            exit_code = proc.poll()
            if exit_code is not None:
                return exit_code
            if cancel.is_cancelled() or time.monotonic() >= deadline:
                # Flip the token (in case timeout fired first) so any
                # downstream watcher knows cancellation happened.
                if not cancel.is_cancelled():
                    cancel.set()
                # Send SIGTERM to the whole group.
                LocalCommandRunner._terminate_process_group(proc)
                try:
                    return proc.wait(timeout=grace + 5.0)
                except (
                    subprocess.TimeoutExpired
                ):  # pragma: no cover  # SIGTERM-ignoring process: escalation path
                    # Escalate to SIGKILL on the group.
                    LocalCommandRunner._kill_process_group(proc)  # pragma: no cover
                    return proc.wait(timeout=2.0)  # pragma: no cover
            time.sleep(poll_interval)

    @staticmethod
    def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
        """Send SIGTERM to the entire process group of ``proc``."""
        try:
            os.killpg(proc.pid, _signal.SIGTERM)
        except (OSError, ProcessLookupError, AttributeError):
            # Non-POSIX (no killpg) or group already gone.
            with contextlib.suppress(Exception):
                proc.terminate()
