"""Cluster C — isolated execution sandbox.

The sandbox layer enforces what the path-authorization layer only
*checks*: agent-generated code may not read files outside the
allowlist, may not hit the network beyond the configured destinations,
may not exfiltrate secrets, may not exceed CPU/memory/disk/time
budgets, and may not fork-bomb.

Public surface:

- :class:`SandboxProfile` — Pydantic model declaring the constraints.
- :class:`SandboxExecutor` — Protocol every executor implements.
- :class:`SandboxResult` — outcome of a sandboxed invocation.
- :class:`NoopSandbox` — default; preserves pre-cluster-C behaviour.
- :class:`DockerSandbox` — Docker-backed isolation (story C2).
- :class:`SubprocessSandbox` — stdlib fallback (story C3).

Wire-in points (story C4): :class:`TaskExecutionService` and the
:class:`SubprocessRunner` constructor accept an optional
``sandbox`` parameter that defaults to :class:`NoopSandbox` so
existing callers continue to work.
"""

from __future__ import annotations

import subprocess  # nosec B404 - we invoke callers' commands intentionally
import time
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from seharness.sandbox.profile import SandboxProfile

__all__ = [
    "DockerSandbox",
    "NoopSandbox",
    "SandboxExecutor",
    "SandboxProfile",
    "SandboxResult",
    "SubprocessSandbox",
]


@runtime_checkable
class SandboxExecutor(Protocol):
    """Strategy interface every sandbox implementation implements.

    Implementations are responsible for:

    - Honouring ``profile.cwd`` (the working directory of the child).
    - Restricting filesystem reads/writes to ``profile.allowed_paths``.
    - Blocking network egress except to ``profile.allowed_network_destinations``.
    - Scrubbing env vars listed in ``profile.denied_env_vars`` from the
      inherited environment before launching the child.
    - Enforcing ``profile.cpu_seconds`` / ``memory_bytes`` /
      ``disk_bytes`` / ``pids_limit`` budgets.
    """

    def run(
        self,
        command: str,
        *,
        profile: SandboxProfile,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> SandboxResult:  # pragma: no cover - structural
        """Run ``command`` under the configured ``profile``.

        Parameters
        ----------
        command:
            Shell command line (callers control the input).
        profile:
            Isolation profile to apply.
        env:
            Optional extra environment to expose to the child
            (merged with ``os.environ`` after scrubbing
            ``profile.denied_env_vars``).
        stdin:
            Optional string piped to the child's stdin.
        """
        ...


class SandboxResult(BaseModel):
    """Outcome of a sandboxed command invocation.

    Mirrors :class:`seharness.validation.runner.CommandResult` so the
    validation pipeline can consume sandbox output without translation.
    ``sandbox_violations`` records any pre-execution rejections
    (e.g. empty allowlist, denied env var references) for telemetry.
    ``command`` may be empty (used to signal "command rejected before
    launch") — unlike ``CommandResult.command`` which is min_length=1.

    ``cancelled`` is True when the run was terminated by
    :class:`seharness.sandbox.cancellation.CancellationToken`. In that
    case ``exit_code`` is -1 (sentinel for \"no natural exit code\")
    and ``stderr`` includes a marker line so logs can distinguish
    cancellation from natural timeouts (which use exit_code 124).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = ""
    exit_code: int = Field(ge=-1)
    stdout: str
    stderr: str
    duration_s: float = Field(ge=0)
    sandbox_violations: tuple[str, ...] = ()
    cancelled: bool = False


class NoopSandbox:
    """Default ``SandboxExecutor`` that runs commands unsandboxed.

    Preserves pre-cluster-C behaviour: callers that don't opt into a
    ``SandboxProfile`` keep the existing straight-subprocess semantics.
    Wire-in points (story C4) default to this implementation so existing
    :class:`TaskExecutionService` and :class:`SubprocessRunner` callers
    continue to work without modification.
    """

    def run(
        self,
        command: str,
        *,
        profile: SandboxProfile,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> SandboxResult:
        start = time.monotonic()
        completed = subprocess.run(  # nosec B602 - caller controls the command
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            input=stdin,
            cwd=profile.cwd,
        )
        return SandboxResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=time.monotonic() - start,
            sandbox_violations=(),
        )


# Late imports so docker / subprocess sandbox code is only loaded when used.
from seharness.sandbox.docker import DockerSandbox  # noqa: E402
from seharness.sandbox.subprocess_sandbox import SubprocessSandbox  # noqa: E402
