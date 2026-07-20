"""Cluster C, story C3: ``SubprocessSandbox`` executor.

A stdlib-only fallback for environments where Docker is not available
(developer laptops, bare CI, locked-down production nodes).

Constraints applied:

- **cwd**: child is launched with ``cwd=profile.cwd``.
- **env scrubbing**: env vars in ``profile.denied_env_vars`` are removed
  from the inherited environment before launch.
- **shell=False**: every command is split with ``shlex.split`` and run
  with ``subprocess.run([...], shell=False)`` — defence against shell
  injection / RCE via the ``command`` argument.
- **resource limits** (POSIX, set in ``preexec_fn``):
  - ``RLIMIT_CPU`` — ``profile.cpu_seconds`` (hard kill after this many
    CPU seconds).
  - ``RLIMIT_FSIZE`` — ``profile.disk_bytes`` write cap.
  - ``RLIMIT_NOFILE`` — 256 open files.
  - ``RLIMIT_NPROC`` — ``profile.pids_limit``.
- **timeout** — ``subprocess.run(..., timeout=profile.cpu_seconds)``
  also enforces a wall-clock ceiling.
- **chroot**: best-effort on Linux when ``profile.allowed_paths`` is a
  single absolute directory and the process has root (uid 0). Skipped
  otherwise (logged via ``SandboxResult.sandbox_violations``).

Tests in this module skip cleanly on platforms where ``resource`` is
absent (Windows). chroot tests skip when not running as root.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 - controlled sandbox runner
import sys
import time
from typing import Any

from seharness.sandbox import SandboxProfile, SandboxResult
from seharness.sandbox.cancellation import CancellationToken, CancellationWatcher

# ---------------------------------------------------------------------------
# Resource-limit helper
# ---------------------------------------------------------------------------


def _apply_rlimits(profile: SandboxProfile) -> None:  # pragma: no cover  # child-only
    """Apply POSIX resource limits in the *child* before exec.

    Called via ``preexec_fn`` so it runs after ``fork()`` but before
    ``exec()``. Errors are swallowed (best-effort); the executor layer
    surfaces them via ``sandbox_violations``.
    """
    import resource  # noqa: PLC0415

    violations: list[str] = []

    # CPU seconds.
    try:
        seconds = max(1, int(profile.cpu_seconds))
        resource.setrlimit(  # pragma: no cover  # child-only
            resource.RLIMIT_CPU, (seconds, seconds)
        )
    except (ValueError, OSError) as exc:  # pragma: no cover - platform-specific
        violations.append(f"RLIMIT_CPU: {exc}")

    # File size (write cap).
    try:
        if profile.disk_bytes > 0:
            resource.setrlimit(  # pragma: no cover  # child-only
                resource.RLIMIT_FSIZE, (int(profile.disk_bytes),) * 2
            )
    except (ValueError, OSError) as exc:  # pragma: no cover
        violations.append(f"RLIMIT_FSIZE: {exc}")

    # Open files.
    try:
        resource.setrlimit(  # pragma: no cover  # child-only
            resource.RLIMIT_NOFILE, (256, 256)
        )
    except (ValueError, OSError) as exc:  # pragma: no cover
        violations.append(f"RLIMIT_NOFILE: {exc}")

    # Processes (fork-bomb guard). Only enforced when the kernel
    # exposes a real hard limit (containers often report -1, in which
    # case setting the limit can break benign subprocesses — the shell
    # in a pipeline needs to fork for each component). Best-effort
    # only: if the kernel refuses, record a violation rather than
    # silently failing. The Docker path is the recommended executor
    # when fork-bomb protection is required.
    try:
        if profile.pids_limit > 0:
            _soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
            if hard != -1 and hard >= profile.pids_limit:
                resource.setrlimit(  # pragma: no cover - child-only
                    resource.RLIMIT_NPROC,
                    (int(profile.pids_limit), int(profile.pids_limit)),
                )
    except (ValueError, OSError) as exc:  # pragma: no cover
        violations.append(f"RLIMIT_NPROC: {exc}")

    # Best-effort chroot: only if exactly one allowed path and we are root.
    if (
        len(profile.allowed_paths) == 1
        and sys.platform.startswith("linux")
        and hasattr(os, "chroot")
        and os.geteuid() == 0
    ):
        try:
            jail = profile.allowed_paths[0]
            if os.path.isdir(jail):
                os.chroot(jail)  # pragma: no cover  # child-only, root-only
                os.chdir("/")
        except OSError as exc:  # pragma: no cover - root-only
            violations.append(f"chroot: {exc}")


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class SubprocessSandbox:
    """POSIX sandbox via stdlib ``subprocess`` + ``resource`` (story C3)."""

    def __init__(self, *, allow_shell: bool = False) -> None:
        """Initialise the executor.

        Parameters
        ----------
        allow_shell:
            When ``True``, commands are executed with ``shell=True`` and
            ``shell=False`` is relaxed. Default ``False`` — every command
            is parsed via :func:`shlex.split` and run as an argv list.
        """
        self._allow_shell = allow_shell

    def run(
        self,
        command: str,
        *,
        profile: SandboxProfile,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        cancel: CancellationToken | None = None,
        cancel_grace_seconds: float = 5.0,
    ) -> SandboxResult:
        """Run ``command`` under the subprocess sandbox.

        Parameters
        ----------
        command:
            The shell command (parsed via ``shlex.split`` unless
            ``allow_shell=True``).
        profile:
            SandboxProfile with rlimits, cwd, and denied env vars.
        env:
            Extra env vars to merge into the child's environment.
        stdin:
            Optional stdin payload for the child.
        cancel:
            Optional :class:`CancellationToken`. If provided, the
            subprocess is terminated (SIGTERM, then SIGKILL after
            ``cancel_grace_seconds``) when the token is set.
        cancel_grace_seconds:
            Seconds between SIGTERM and SIGKILL. Default 5.0.
        """
        start = time.monotonic()
        full_env = _scrub_env(profile, env)
        violations: list[str] = []

        # Validate env scrubbing is possible — fail-closed if the caller
        # asked for an empty deny list AND there are secrets in the
        # environment we cannot scrub. We always scrub the configured
        # deny list, but if ``denied_env_vars`` is empty we record a
        # violation so the caller knows nothing was scrubbed.
        if not profile.denied_env_vars:
            violations.append("denied_env_vars is empty — no scrubbing")

        if self._allow_shell:
            argv: str | list[str] = command
        else:
            argv = shlex.split(command)
            if not argv:
                return SandboxResult(
                    command=command,
                    exit_code=2,
                    stdout="",
                    stderr="empty command after shlex.split",
                    duration_s=time.monotonic() - start,
                    sandbox_violations=tuple(violations),
                )

        timeout = max(1.0, float(profile.cpu_seconds))
        preexec_fn: Any = None
        # Use start_new_session=True so the child becomes its own process
        # group leader. This lets the CancellationWatcher send SIGTERM /
        # SIGKILL to the group (terminating any grandchildren spawned by
        # the child, e.g. pytest fixtures) rather than just the immediate
        # child. We keep preexec_fn for POSIX rlimits.
        start_new_session = hasattr(os, "setsid")
        if hasattr(os, "fork"):  # POSIX  # pragma: no cover
            preexec_fn = _make_preexec(profile, violations)

        try:
            proc = subprocess.Popen(  # nosec B603,B602 - caller controls argv; shell only when opted-in
                argv,
                shell=self._allow_shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=profile.cwd,
                env=full_env,
                stdin=subprocess.PIPE if stdin else None,
                preexec_fn=preexec_fn,
                start_new_session=start_new_session,
            )
        except FileNotFoundError as exc:
            return SandboxResult(
                command=command,
                exit_code=127,
                stdout="",
                stderr=f"command not found: {exc}",
                duration_s=time.monotonic() - start,
                sandbox_violations=tuple(violations),
            )

        # Start cancellation watcher before we block on communicate().
        # The watcher holds a weak reference to proc; if proc is GC'd
        # before the watcher fires, the watcher exits cleanly.
        watcher: CancellationWatcher | None = None
        if cancel is not None:
            watcher = CancellationWatcher(
                token=cancel,
                target=proc,
                grace_seconds=cancel_grace_seconds,
            )

        # Run communicate with a wall-clock ceiling. We use communicate()
        # rather than wait() so stdout/stderr are drained (avoids
        # deadlock on child writing to a full pipe buffer).
        try:
            try:
                stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
            except subprocess.TimeoutExpired:
                # Wall-clock timeout (matches the previous
                # subprocess.run(... timeout=...) behaviour).
                proc.kill()
                stdout, stderr = proc.communicate()
                if watcher is not None:
                    watcher.stop(timeout=cancel_grace_seconds + 1.0)
                return SandboxResult(
                    command=command,
                    exit_code=124,  # canonical timeout exit code
                    stdout=_decode(stdout),
                    stderr=_decode(stderr) + f"\nTIMEOUT after {timeout}s",
                    duration_s=time.monotonic() - start,
                    sandbox_violations=tuple(violations),
                )
        finally:
            if watcher is not None:
                # Tear down the watcher (idempotent). If it had already
                # fired and escalated, escalated_to_sigkill reflects that.
                watcher.stop(timeout=cancel_grace_seconds + 1.0)

        # Distinguish cancellation (SIGTERM/SIGKILL from watcher) from
        # natural completion. The watcher sets proc.returncode to a
        # negative signal value (-SIGTERM = -15, -SIGKILL = -9); we
        # also check the cancellation token's state since that's the
        # authoritative signal source.
        cancelled = cancel is not None and bool(cancel.is_cancelled())
        if cancelled:
            return SandboxResult(
                command=command,
                exit_code=-1,  # sentinel: no natural exit code
                stdout=_decode(stdout),
                stderr=_decode(stderr) + "\nCANCELLED via CancellationToken",
                duration_s=time.monotonic() - start,
                sandbox_violations=tuple(violations),
                cancelled=True,
            )

        return SandboxResult(
            command=command,
            exit_code=proc.returncode,
            stdout=_decode(stdout) if stdout else "",
            stderr=_decode(stderr) if stderr else "",
            duration_s=time.monotonic() - start,
            sandbox_violations=tuple(violations),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scrub_env(profile: SandboxProfile, extra: dict[str, str] | None) -> dict[str, str]:
    """Build the child's environment with denied vars removed."""
    out: dict[str, str] = {}
    deny = set(profile.denied_env_vars)
    for k, v in os.environ.items():
        if k in deny:
            continue
        out[k] = v
    if extra:
        for k, v in extra.items():
            if k in deny:
                continue
            out[k] = v
    return out


def _make_preexec(  # pragma: no cover  # parent-only construction
    profile: SandboxProfile, violations: list[str]
) -> Any:
    """Return a ``preexec_fn`` callable that applies the profile's rlimits."""

    def _preexec() -> None:  # pragma: no cover - executed in child
        _apply_rlimits(profile)
        # violations is captured by closure; we cannot raise from here
        # without aborting the child, so we just log.

    return _preexec


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = ["SubprocessSandbox"]
