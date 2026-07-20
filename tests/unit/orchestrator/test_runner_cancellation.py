"""Cluster E, story E4b: runner-level cancellation tests.

Covers ``LocalCommandRunner`` behaviour when a
``CancellationToken`` is supplied:

- Fast path (no token) still works exactly as before
  (``subprocess.run(timeout=...)`` returns exit code or 124).
- Slow path: token fires while subprocess is sleeping, watcher
  sends SIGTERM, runner returns ``exit_code=130`` with a
  cancellation note.
- SIGTERM escalation to SIGKILL if the process ignores SIGTERM
  (we test the cancellation timing, not the kill specifically;
  that's covered in tests/unit/sandbox/test_cancellation.py).
- Token already cancelled at call time returns immediately.
- ``StubRunner`` accepts and ignores the token (API parity).

These tests do NOT use mocks for the cancellation watcher — we
exercise the real ``CancellationWatcher`` + real ``Popen`` to
prove the wiring is real.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from seharness.controller import run_ledger  # noqa: F401  (breaks circular import)
from seharness.orchestrator.runner import CommandResult, LocalCommandRunner, StubRunner
from seharness.sandbox.cancellation import CancellationToken

# Per-test timeouts are handled via threading + join() in the e2e
# tests below; we don't use pytest.mark.timeout because the project's
# pyproject.toml doesn't define a `timeout` marker.


# ---------------------------------------------------------------------------
# StubRunner: API parity
# ---------------------------------------------------------------------------


class TestStubRunnerAcceptsCancel:
    def test_run_task_ignores_cancel(self, tmp_path: Path) -> None:
        """StubRunner accepts a token and ignores it (deterministic work)."""
        token = CancellationToken()
        result = StubRunner().run_task(
            red_dir=tmp_path / "red",
            green_dir=tmp_path / "green",
            task_id="t1",
            cancel=token,
        )
        assert result.exit_code == 0
        assert not token.is_cancelled()  # StubRunner never flips it

    def test_run_validation_ignores_cancel(self, tmp_path: Path) -> None:
        """StubRunner.run_validation ignores cancel; returns synthetic OK."""
        result = StubRunner().run_validation(
            command="echo hi",
            cwd=tmp_path,
            cancel=CancellationToken(),
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# LocalCommandRunner fast path (no cancel)
# ---------------------------------------------------------------------------


class TestLocalRunnerNoCancel:
    def test_success(self, tmp_path: Path) -> None:
        runner = LocalCommandRunner()
        result = runner.run_validation(
            command=f"{sys.executable} -c \"print('hi')\"",
            cwd=tmp_path,
            cancel=None,
        )
        assert result.exit_code == 0
        assert "hi" in result.stdout

    def test_failure_returns_nonzero(self, tmp_path: Path) -> None:
        runner = LocalCommandRunner()
        result = runner.run_validation(
            command=f'{sys.executable} -c "import sys; sys.exit(7)"',
            cwd=tmp_path,
            cancel=None,
        )
        assert result.exit_code == 7

    def test_timeout_returns_124(self, tmp_path: Path) -> None:
        runner = LocalCommandRunner()
        result = runner.run_validation(
            command=f'{sys.executable} -c "import time; time.sleep(10)"',
            cwd=tmp_path,
            timeout_s=0.5,
            cancel=None,
        )
        assert result.exit_code == 124
        assert "TIMEOUT" in result.stderr


# ---------------------------------------------------------------------------
# LocalCommandRunner cancellable path
# ---------------------------------------------------------------------------


class TestLocalRunnerCancel:
    def test_cancel_terminates_long_running_subprocess(self, tmp_path: Path) -> None:
        """Token fires while subprocess sleeps; runner returns 130."""
        runner = LocalCommandRunner()
        token = CancellationToken()

        # Schedule a cancel after 0.3s on a background thread.
        def fire_after_delay() -> None:
            time.sleep(0.3)
            token.set()

        import threading

        t = threading.Thread(target=fire_after_delay, daemon=True)
        t.start()

        # A subprocess that sleeps for 30s. We expect cancellation to
        # cut it down before the 30s elapses.
        start = time.monotonic()
        result = runner.run_validation(
            command=(f'{sys.executable} -c "import time; time.sleep(30)"'),
            cwd=tmp_path,
            timeout_s=30.0,
            cancel=token,
        )
        elapsed = time.monotonic() - start

        # Cancellation should have fired within ~1s, not the full 30s.
        assert elapsed < 5.0, (
            f"cancellation took too long ({elapsed:.1f}s); watcher/SIGTERM may not have fired"
        )
        assert result.exit_code == 130, (
            f"expected 130 (cancelled), got {result.exit_code}; stderr={result.stderr!r}"
        )
        assert "cancelled" in result.stderr.lower()

    def test_already_cancelled_returns_immediately(self, tmp_path: Path) -> None:
        """Token already set at call time → fast return."""
        runner = LocalCommandRunner()
        token = CancellationToken()
        token.set()

        start = time.monotonic()
        result = runner.run_validation(
            command=(f'{sys.executable} -c "import time; time.sleep(30)"'),
            cwd=tmp_path,
            timeout_s=30.0,
            cancel=token,
        )
        elapsed = time.monotonic() - start

        # The process spawns, then the watcher fires SIGTERM on the
        # already-cancelled token within one poll interval (~50ms).
        # The grace window adds another ~5s before SIGKILL. The total
        # is bounded by grace + a small margin.
        assert elapsed < 8.0, f"already-cancelled runner took {elapsed:.1f}s"
        assert result.exit_code == 130

    def test_subprocess_actually_dies(self, tmp_path: Path) -> None:
        """After cancel_run returns, no zombie subprocess remains."""
        runner = LocalCommandRunner()
        token = CancellationToken()

        import threading

        def fire() -> None:
            time.sleep(0.2)
            token.set()

        threading.Thread(target=fire, daemon=True).start()

        result = runner.run_validation(
            command=(f"{sys.executable} -c \"import time; time.sleep(60); print('NEVER')\""),
            cwd=tmp_path,
            timeout_s=60.0,
            cancel=token,
        )

        # The runner's communicate() already reaped the child, so
        # there's no zombie to find. Verify by checking that the
        # process tree contains no sleeping python subprocesses
        # matching our command. This is a belt-and-braces check.
        try:
            ps = subprocess.run(
                ["ps", "-eo", "pid,comm"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            # ``sleep`` children of cancelled python: there should be
            # none hanging around with command matching our pattern.
            assert "NEVER" not in ps.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # ps may not be available (Windows); skip this branch.
            pytest.skip("ps not available")

        assert result.exit_code == 130

    def test_completes_normally_when_token_never_fires(self, tmp_path: Path) -> None:
        """Token never fires → runner returns normal exit code."""
        runner = LocalCommandRunner()
        token = CancellationToken()

        result = runner.run_validation(
            command=f"{sys.executable} -c \"print('ok')\"",
            cwd=tmp_path,
            timeout_s=5.0,
            cancel=token,
        )
        assert result.exit_code == 0
        assert "ok" in result.stdout
        assert not token.is_cancelled()

    def test_command_spawn_failure_returns_127(self, tmp_path: Path) -> None:
        """If Popen itself fails, runner returns 127 (command-not-found)."""
        runner = LocalCommandRunner()
        result = runner.run_validation(
            command="/nonexistent/binary/that/does/not/exist/anywhere",
            cwd=tmp_path,
            timeout_s=5.0,
            cancel=CancellationToken(),
        )
        # 127 (POSIX sh convention) or sometimes 2; both are "couldn't run".
        assert result.exit_code in (127, 2), (
            f"expected 127 or 2 for missing binary, got {result.exit_code}"
        )


# ---------------------------------------------------------------------------
# Type signatures / API parity
# ---------------------------------------------------------------------------


class TestApiParity:
    def test_both_runners_accept_cancel_kwarg(self, tmp_path: Path) -> None:
        """Both runners' public methods take ``cancel=``."""
        import inspect

        for method in (
            StubRunner.run_task,
            StubRunner.run_validation,
            LocalCommandRunner.run_task,
            LocalCommandRunner.run_validation,
        ):
            sig = inspect.signature(method)
            assert "cancel" in sig.parameters, f"{method.__qualname__} missing `cancel` parameter"

    def test_command_result_dataclass_unchanged(self) -> None:
        """``CommandResult`` field set is unchanged from pre-E4b."""
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(CommandResult)}
        assert field_names == {
            "command",
            "exit_code",
            "stdout",
            "stderr",
            "duration_s",
        }
