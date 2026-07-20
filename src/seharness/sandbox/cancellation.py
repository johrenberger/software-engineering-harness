"""Cluster E, story E4: subprocess cancellation primitive.

This module provides:

- :class:`CancellationToken`: a thread-safe one-shot cancellation signal
  that any number of threads can poll or wait on.
- :class:`CancellationWatcher`: a background thread that monitors a
  ``CancellationToken`` and, on activation, sends ``SIGTERM`` to a
  target ``subprocess.Popen`` process. After a grace period it sends
  ``SIGKILL``.

Design notes:

- The token is single-shot. Once cancelled it stays cancelled.
- The watcher is daemon-threaded so it never blocks interpreter
  shutdown.
- The watcher holds a weak reference to the ``Popen`` object so it
  cannot keep a finished process alive after ``communicate()``
  reaps it.
- The grace period is configurable per watcher (default 5 s).
- The watcher is best-effort: if ``terminate()`` or ``kill()`` raises
  (process already gone, permission denied), the watcher logs and
  exits cleanly. Cancellation is not an excuse to crash the parent.

This module is intentionally self-contained so it can be imported from
both ``seharness.sandbox.subprocess_sandbox`` and the orchestrator
without creating import cycles.
"""

from __future__ import annotations

import contextlib
import logging
import signal as _signal
import threading
import time
import weakref
from types import FrameType
from typing import Protocol

_LOG = logging.getLogger(__name__)


class SupportsTerminate(Protocol):
    """Subset of :class:`subprocess.Popen` that we need.

    Using a Protocol avoids importing subprocess at module top-level so
    this file stays cheap to import.
    """

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int | None: ...


class CancellationToken:
    """Thread-safe one-shot cancellation signal.

    Example:
        token = CancellationToken()
        token.set()              # any thread can cancel
        if token.is_cancelled(): # any thread can check
            ...
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def is_cancelled(self) -> bool:
        """Return True if ``set()`` has been called."""
        return self._event.is_set()

    def set(self) -> None:
        """Activate the cancellation signal. Idempotent."""
        if not self._event.is_set():
            self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancellation or timeout.

        Returns True if cancelled, False if timed out.
        """
        return self._event.wait(timeout=timeout)

    def reset(self) -> None:
        """Clear the signal so the token can be reused.

        Provided for symmetry; in practice CancellationToken is
        single-use per cancellation cycle.
        """
        self._event.clear()


class CancellationWatcher:
    """Background thread that sends signals to a ``Popen`` on cancel.

    The watcher holds a *weak reference* to the target so it cannot
    keep a finished process alive after ``communicate()`` reaps it.

    Parameters
    ----------
    token:
        The cancellation token to watch.
    target:
        The subprocess-like object to terminate on cancellation.
    grace_seconds:
        Seconds to wait between SIGTERM and SIGKILL. Default 5.
    poll_interval:
        Seconds between polls of the token. Default 0.1.
    """

    def __init__(
        self,
        *,
        token: CancellationToken,
        target: SupportsTerminate,
        grace_seconds: float = 5.0,
        poll_interval: float = 0.1,
    ) -> None:
        self._token = token
        self._target_ref: weakref.ref[SupportsTerminate] = weakref.ref(target)
        self._grace_seconds = float(grace_seconds)
        self._poll_interval = float(poll_interval)
        self._thread = threading.Thread(
            target=self._run,
            name="seharness-cancellation-watcher",
            daemon=True,
        )
        self._stop_requested = threading.Event()
        self._finished_sigkill = threading.Event()
        self._thread.start()

    @property
    def thread(self) -> threading.Thread:
        """The underlying watcher thread (for tests / introspection)."""
        return self._thread

    def stop(self, timeout: float | None = None) -> None:
        """Signal the watcher to exit and wait for it.

        Used by ``SubprocessSandbox.run`` to cleanly tear the watcher
        down after the subprocess completes (regardless of whether
        cancellation fired).
        """
        self._stop_requested.set()
        self._thread.join(timeout=timeout)

    @property
    def escalated_to_sigkill(self) -> bool:
        """True if the watcher had to escalate to SIGKILL."""
        return self._finished_sigkill.is_set()

    def _run(self) -> None:
        """Watcher loop: poll token, terminate target on activation."""
        deadline: float | None = None
        while not self._stop_requested.is_set():
            if self._token.is_cancelled():
                target = self._target_ref()
                if target is None:
                    return  # target was already GC'd; nothing to do
                self._terminate_and_maybe_kill(target, deadline)
                return
            if deadline is not None:
                # We're inside the SIGTERM grace period; check whether
                # the process has exited on its own.
                target = self._target_ref()
                if target is None or target.poll() is not None:
                    return
                if time.monotonic() >= deadline:
                    self._escalate(target)
                    return
            self._stop_requested.wait(self._poll_interval)

    def _terminate_and_maybe_kill(self, target: SupportsTerminate, deadline: float | None) -> None:
        """Send SIGTERM, start the grace-period countdown."""
        try:
            target.terminate()
        except (OSError, ProcessLookupError) as exc:
            _LOG.info("CancellationWatcher: terminate() failed: %s", exc)
            return
        # If the caller already provided a deadline (re-entry), honor it.
        if deadline is None:
            deadline = time.monotonic() + self._grace_seconds
        # Loop in main _run until either: process exits, deadline
        # elapses, or stop is requested.
        while not self._stop_requested.is_set():
            if target.poll() is not None:
                return
            if time.monotonic() >= deadline:
                self._escalate(target)
                return
            self._stop_requested.wait(self._poll_interval)

    def _escalate(self, target: SupportsTerminate) -> None:
        """Send SIGKILL after the grace period elapsed."""
        try:
            target.kill()
            self._finished_sigkill.set()
        except (OSError, ProcessLookupError) as exc:
            _LOG.info("CancellationWatcher: kill() failed: %s", exc)


def install_sigint_handler(token: CancellationToken) -> None:  # pragma: no cover
    """Replace ``SIGINT`` (Ctrl-C) with a token-based cancel path.

    Useful for CLI entrypoints that want graceful shutdown via
    ``CancellationToken`` instead of the default ``KeyboardInterrupt``.

    Parameters
    ----------
    token:
        The token to set when SIGINT fires.

    Note
    ----
    This function is NOT yet wired into any CLI entrypoint. It ships
    here so the cancellation primitive is complete and can be wired
    up in a follow-up slice without re-exporting anything. The
    ``# pragma: no cover`` reflects that it has no test or caller
    today; do not remove the pragma until the first caller lands.

    ``signal.signal`` only works on the main thread. Calling this from
    a worker thread is a no-op (returns silently). This is a known
    stdlib constraint, not a bug.
    """
    try:
        handler = _signal.getsignal(_signal.SIGINT)
    except (ValueError, OSError):
        return  # not on main thread, or signal unavailable

    def _cancel_on_sigint(signum: int, frame: FrameType | None) -> None:
        token.set()
        # If a previous handler was installed, defer to it after
        # marking cancellation so the user sees the cancel take effect.
        # After `callable(handler)`, mypy narrows the union; the int
        # constants SIG_DFL/SIG_IGN cannot be callable, so we can call
        # `handler` directly here.
        if callable(handler):
            with contextlib.suppress(BaseException):
                handler(signum, frame)

    try:
        _signal.signal(_signal.SIGINT, _cancel_on_sigint)
    except (ValueError, OSError):
        return


__all__ = [
    "CancellationToken",
    "CancellationWatcher",
    "SupportsTerminate",
    "install_sigint_handler",
]
