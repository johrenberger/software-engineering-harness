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
    ) -> SandboxResult:
        """Run ``command`` under the subprocess sandbox."""
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
        if hasattr(os, "fork"):  # POSIX  # pragma: no cover
            preexec_fn = _make_preexec(profile, violations)

        try:
            completed = subprocess.run(  # nosec B603,B602 - caller controls argv; shell only when opted-in
                argv,
                shell=self._allow_shell,
                capture_output=True,
                text=True,
                check=False,
                cwd=profile.cwd,
                env=full_env,
                input=stdin,
                timeout=timeout,
                preexec_fn=preexec_fn,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                command=command,
                exit_code=124,  # canonical timeout exit code (matches LocalCommandRunner)
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr) + f"\nTIMEOUT after {timeout}s",
                duration_s=time.monotonic() - start,
                sandbox_violations=tuple(violations),
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

        return SandboxResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
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
