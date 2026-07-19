"""Validation runner primitives for slice 7.

Per SPEC \u00a7"Validation runner" and slice 7 RED bullet 1, every failed
command produces a ``NormalizedFailure`` \u2014 a structured record with
stable fields (kind, exit_code, duration, command, message, source).

This module owns the *types* and the *protocol*:

- ``CommandResult`` \u2014 raw outcome of a subprocess invocation.
- ``NormalizedFailure`` \u2014 structured failure record produced by the
  classifier (in ``classifier.py``).
- ``FailureKind`` \u2014 closed ``StrEnum`` with the documented buckets.
- ``ValidationRunner`` \u2014 protocol: ``run(command: str) -> CommandResult``.
- ``SubprocessRunner`` \u2014 default implementation that actually invokes
  ``subprocess.run``.

Design notes:

- Pydantic ``ConfigDict(extra=\"forbid\", frozen=True)`` \u2014 typos surface
  immediately, and JSON on disk is the canonical shape. ``exit_code``
  has ``ge=-1`` because some test runners report ``-1`` to mean "killed
  by signal".
- ``FailureKind`` is a closed ``StrEnum`` (5 buckets: ASSERTION,
  COLLECTION_ERROR, TIMEOUT, INFRASTRUCTURE, GENERIC_NONZERO).
- ``ValidationRunner`` is a ``Protocol`` so callers can inject stubs.
- Cluster C, story C4: ``SubprocessRunner`` accepts an optional
  ``sandbox`` (``SandboxExecutor``) and ``sandbox_profile``
  (``SandboxProfile``). Default ``NoopSandbox()`` preserves the
  pre-cluster-C behaviour.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from seharness.sandbox import SandboxExecutor, SandboxProfile


class FailureKind(StrEnum):
    """Closed set of failure reasons for a command outcome.

    Per SPEC \u00a7"Validation runner", the harness recognises five
    buckets; everything else is rejected as "unknown failure kind" so
    the classifier cannot smuggle in a fake passing test.
    """

    ASSERTION = "assertion"
    COLLECTION_ERROR = "collection_error"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    GENERIC_NONZERO = "generic_nonzero"


class CommandResult(BaseModel):
    """Raw outcome of a subprocess invocation.

    ``exit_code`` is the process return code (``ge=-1`` because signal
    kills are reported as ``-1`` on POSIX). ``duration_s`` is wall-clock
    seconds (``ge=0``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: str = Field(min_length=1)
    exit_code: int = Field(ge=-1)
    stdout: str
    stderr: str
    duration_s: float = Field(ge=0)


class NormalizedFailure(BaseModel):
    """Structured failure record produced by ``FailureClassifier``.

    ``source`` is ``\"stdout\"`` or ``\"stderr\"`` \u2014 which stream the
    failure message came from. ``duration_s`` matches the original
    ``CommandResult.duration_s``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FailureKind
    exit_code: int = Field(ge=-1)
    command: str = Field(min_length=1)
    message: str
    source: str = Field(pattern="^(stdout|stderr)$")
    duration_s: float = Field(ge=0)


@runtime_checkable
class ValidationRunner(Protocol):
    """Protocol: ``run(command: str) -> CommandResult``.

    Callers inject concrete implementations (e.g. ``SubprocessRunner``,
    a test stub, or a future ``PytestRunner``).
    """

    def run(self, command: str) -> CommandResult:  # pragma: no cover - structural
        ...


class SubprocessRunner:
    """Default ``ValidationRunner`` that invokes ``subprocess.run``.

    Captures stdout/stderr, exit code, and wall-clock duration. Does
    NOT raise on non-zero exit \u2014 it surfaces the exit code in
    ``CommandResult.exit_code`` for the classifier to consume.

    Cluster C, story C4: optionally accepts a ``sandbox`` and
    ``sandbox_profile``. When supplied, every command runs through the
    sandbox executor (translating the returned ``SandboxResult`` back
    to a ``CommandResult`` so existing callers see no API change).
    Default ``sandbox=NoopSandbox()`` preserves pre-cluster-C
    behaviour.
    """

    def __init__(
        self,
        *,
        sandbox: SandboxExecutor | None = None,
        sandbox_profile: SandboxProfile | None = None,
    ) -> None:
        from seharness.sandbox import (  # noqa: PLC0415
            NoopSandbox,
            SandboxExecutor,
            SandboxProfile,
        )

        if sandbox is None:
            sandbox = NoopSandbox()
        if not isinstance(sandbox, SandboxExecutor):
            raise TypeError(
                f"sandbox must be a SandboxExecutor, got {type(sandbox).__name__}"
            )
        if sandbox_profile is None:
            sandbox_profile = SandboxProfile()
        if not isinstance(sandbox_profile, SandboxProfile):
            raise TypeError(
                "sandbox_profile must be a SandboxProfile or None, "
                f"got {type(sandbox_profile).__name__}"
            )
        self._sandbox: SandboxExecutor = sandbox
        self._sandbox_profile: SandboxProfile = sandbox_profile

    def run(self, command: str) -> CommandResult:
        from seharness.sandbox import SandboxResult  # noqa: PLC0415

        result = self._sandbox.run(command, profile=self._sandbox_profile)
        if isinstance(result, SandboxResult):
            return CommandResult(
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_s=result.duration_s,
            )
        # Allow test doubles that already return CommandResult-shaped objects.
        return CommandResult(  # type: ignore[unreachable]
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_s=result.duration_s,
        )


__all__ = [
    "CommandResult",
    "FailureKind",
    "NormalizedFailure",
    "SubprocessRunner",
    "ValidationRunner",
]
