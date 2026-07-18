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
"""

from __future__ import annotations

import subprocess  # nosec B404 - we invoke callers' commands intentionally
import time
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


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
    """

    def run(self, command: str) -> CommandResult:
        start = time.monotonic()
        proc = subprocess.run(  # nosec B602 - callers control the command; shell needed for pipelines/redirection
            command,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed = time.monotonic() - start
        return CommandResult(
            command=command,
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_s=elapsed,
        )


__all__ = [
    "CommandResult",
    "FailureKind",
    "NormalizedFailure",
    "SubprocessRunner",
    "ValidationRunner",
]
