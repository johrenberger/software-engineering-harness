"""RED tests for Cluster E, story E4: subprocess cancellation.

Covers:

- :class:`CancellationToken` semantics (set / poll / wait / reset).
- :class:`CancellationWatcher` signals SIGTERM, then SIGKILL after grace.
- :class:`SubprocessSandbox.run` honours ``cancel=`` and reports
  ``cancelled=True`` on the result.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# CancellationToken
# ---------------------------------------------------------------------------


class TestCancellationToken:
    def test_starts_uncancelled(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        assert token.is_cancelled() is False

    def test_set_activates(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        token.set()
        assert token.is_cancelled() is True

    def test_set_is_idempotent(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        token.set()
        token.set()
        token.set()
        assert token.is_cancelled() is True

    def test_reset_returns_to_uncancelled(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        token.set()
        token.reset()
        assert token.is_cancelled() is False

    def test_wait_returns_true_when_cancelled(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        token.set()
        assert token.wait(timeout=1.0) is True

    def test_wait_returns_false_on_timeout(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()
        assert token.wait(timeout=0.05) is False

    def test_wait_blocks_until_cancelled(self) -> None:
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        token = CancellationToken()

        def later() -> None:
            time.sleep(0.05)
            token.set()

        t = threading.Thread(target=later)
        t.start()
        # wait() should return True once later() fires.
        assert token.wait(timeout=1.0) is True
        t.join()


# ---------------------------------------------------------------------------
# CancellationWatcher — uses real subprocess.Popen so we test real signals.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
class TestCancellationWatcher:
    def _spawn_sleeper(self) -> subprocess.Popen:
        """Spawn a long-running sleep child (POSIX-only)."""
        return subprocess.Popen(  # noqa: S603,S607 — test-only
            ["python3", "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_sends_sigterm_on_cancel(self) -> None:
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        proc = self._spawn_sleeper()
        try:
            token = CancellationToken()
            watcher = CancellationWatcher(
                token=token, target=proc, grace_seconds=5.0, poll_interval=0.02
            )
            token.set()
            # Wait for SIGTERM to be delivered and the child to exit.
            proc.wait(timeout=2.0)
            assert proc.returncode is not None
            # On POSIX, SIGTERM is -15 (signal-derived negative return).
            assert proc.returncode in {-15, -9}  # SIGTERM or SIGKILL
            watcher.stop(timeout=2.0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_escalates_to_sigkill_after_grace(self) -> None:
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        # Spawn a child that *ignores* SIGTERM (so SIGKILL must fire).
        # POSIX-only; on Windows this is meaningless. We use a pipe to
        # signal readiness so the test doesn't race against Python's
        # interpreter startup.
        ready_r, ready_w = os.pipe()
        try:
            proc = subprocess.Popen(  # noqa: S603,S607 — test-only
                [
                    "python3",
                    "-c",
                    (
                        "import os, signal, time\n"
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                        f"os.write({ready_w}, b'1')\n"
                        "time.sleep(30)\n"
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(ready_w,),
            )
            # Wait for the child to install its SIGTERM handler.
            os.close(ready_w)
            os.read(ready_r, 1)
            os.close(ready_r)

            token = CancellationToken()
            watcher = CancellationWatcher(
                token=token, target=proc, grace_seconds=0.5, poll_interval=0.02
            )
            token.set()
            proc.wait(timeout=5.0)
            assert proc.returncode == -9, f"expected SIGKILL (-9), got {proc.returncode}"
            assert watcher.escalated_to_sigkill is True
            watcher.stop(timeout=2.0)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        finally:
            for fd in (ready_r, ready_w):
                with contextlib.suppress(OSError):
                    os.close(fd)
            if "proc" in locals() and proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_stop_terminates_watcher_when_uncancelled(self) -> None:
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        proc = self._spawn_sleeper()
        try:
            token = CancellationToken()
            watcher = CancellationWatcher(
                token=token, target=proc, grace_seconds=5.0, poll_interval=0.02
            )
            # No cancellation. stop() should join cleanly without killing.
            watcher.stop(timeout=2.0)
            assert not watcher.thread.is_alive()
            # Process should still be running (we didn't cancel).
            assert proc.poll() is None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_watcher_with_already_dead_target_exits_cleanly(self) -> None:
        """Weak ref handling: if the subprocess exits on its own before
        cancellation fires, the watcher must not crash."""
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        proc = subprocess.Popen(  # noqa: S603,S607 — test-only
            ["python3", "-c", "print('quick')"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=2.0)
        token = CancellationToken()
        watcher = CancellationWatcher(
            token=token, target=proc, grace_seconds=5.0, poll_interval=0.02
        )
        # Token never fires. Watcher must stop cleanly.
        watcher.stop(timeout=2.0)
        assert not watcher.thread.is_alive()

    def test_watcher_handles_target_disappearing_during_grace(self) -> None:
        """If the weakref to the target vanishes while the watcher is
        sleeping, the watcher must exit cleanly without crashing."""
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        token = CancellationToken()

        class _FakeTarget:
            def poll(self) -> int | None:
                return 0  # always dead

            def terminate(self) -> None:
                raise ProcessLookupError("already gone")

            def kill(self) -> None:
                raise ProcessLookupError("already gone")

            def wait(self, timeout: float) -> int | None:
                return 0

        # Wrap so the weakref can vanish; the watcher must not crash.
        fake = _FakeTarget()
        watcher = CancellationWatcher(
            token=token, target=fake, grace_seconds=5.0, poll_interval=0.02
        )
        token.set()
        watcher.stop(timeout=2.0)
        assert not watcher.thread.is_alive()

    def test_escalate_handles_oserror_from_kill(self) -> None:
        """If ``kill()`` raises ``OSError`` (race: process gone), the
        watcher must log and exit without raising."""
        from seharness.sandbox.cancellation import (  # noqa: PLC0415
            CancellationToken,
            CancellationWatcher,
        )

        token = CancellationToken()

        class _DyingTarget:
            def __init__(self) -> None:
                self._alive = True

            def poll(self) -> int | None:
                return None if self._alive else 0

            def terminate(self) -> None:
                # Pretend SIGTERM worked but the process hasn't reaped yet.
                pass

            def kill(self) -> None:
                # Race: process already reaped between SIGTERM and SIGKILL.
                self._alive = False
                raise ProcessLookupError("process gone")

            def wait(self, timeout: float) -> int | None:
                return 0 if not self._alive else None

        target = _DyingTarget()
        watcher = CancellationWatcher(
            token=token, target=target, grace_seconds=0.2, poll_interval=0.02
        )
        token.set()
        watcher.stop(timeout=3.0)
        assert not watcher.thread.is_alive()
        # escalated_to_sigkill must remain False because kill() raised.
        assert watcher.escalated_to_sigkill is False


# ---------------------------------------------------------------------------
# SubprocessSandbox.run with cancel= parameter
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
class TestSubprocessSandboxCancellation:
    def test_cancel_during_long_run_terminates_subprocess(self, tmp_path: Path) -> None:
        """A long sleep terminated mid-flight: result has cancelled=True
        and exit_code is the sentinel -1."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=60.0)
        sandbox = SubprocessSandbox()

        token = CancellationToken()
        result_holder: list = []

        def run_and_cancel() -> None:
            r = sandbox.run(
                "python3 -c 'import time; time.sleep(30)'",
                profile=profile,
                cancel=token,
                cancel_grace_seconds=0.5,
            )
            result_holder.append(r)

        t = threading.Thread(target=run_and_cancel)
        t.start()
        # Let the child start, then cancel.
        time.sleep(0.3)
        token.set()
        t.join(timeout=5.0)
        assert not t.is_alive(), "thread did not finish within 5s"

        assert len(result_holder) == 1
        result = result_holder[0]
        assert result.cancelled is True
        assert result.exit_code == -1
        assert "CANCELLED" in result.stderr

    def test_no_cancel_token_means_cancelled_false(self, tmp_path: Path) -> None:
        """Backwards compatibility: without cancel=, result.cancelled is False."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        result = SubprocessSandbox().run("true", profile=profile)
        assert result.cancelled is False
        assert result.exit_code == 0

    def test_short_command_completes_before_cancel_fires(self, tmp_path: Path) -> None:
        """A fast command that completes naturally before cancel fires
        must NOT be marked cancelled."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        token = CancellationToken()

        def run_and_cancel() -> None:
            # Cancel after a long delay; command finishes long before then.
            time.sleep(2.0)
            token.set()

        t = threading.Thread(target=run_and_cancel)
        t.start()
        result = SubprocessSandbox().run(
            "true", profile=profile, cancel=token, cancel_grace_seconds=0.5
        )
        t.join(timeout=3.0)

        assert result.exit_code == 0
        assert result.cancelled is False
        assert "CANCELLED" not in result.stderr

    def test_sandbox_result_default_cancelled_is_false(self) -> None:
        """``SandboxResult.cancelled`` defaults to False for backwards compat."""
        from seharness.sandbox import SandboxResult  # noqa: PLC0415

        result = SandboxResult(exit_code=0, stdout="", stderr="", duration_s=0.0)
        assert result.cancelled is False

    def test_command_not_found_returns_127(self, tmp_path: Path) -> None:
        """A non-existent command produces exit_code 127 even when cancel= is set."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        token = CancellationToken()
        result = SubprocessSandbox().run(
            "/nonexistent/command_xyz_12345",
            profile=profile,
            cancel=token,
        )
        assert result.exit_code == 127
        assert "not found" in result.stderr.lower()
        assert result.cancelled is False

    def test_wall_clock_timeout_kills_subprocess(self, tmp_path: Path) -> None:
        """A long-running child that exceeds profile.cpu_seconds (wall-clock
        ceiling) is killed and the result reports the canonical exit_code 124."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.sandbox.cancellation import CancellationToken  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=1.0)
        token = CancellationToken()
        result = SubprocessSandbox().run(
            "python3 -c 'import time; time.sleep(30)'",
            profile=profile,
            cancel=token,
            cancel_grace_seconds=0.5,
        )
        assert result.exit_code == 124
        assert "TIMEOUT" in result.stderr
        assert result.cancelled is False
