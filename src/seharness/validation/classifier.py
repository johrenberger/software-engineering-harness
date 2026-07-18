"""Failure classifier for slice 7.

Per SPEC \u00a7"Validation runner", ``FailureClassifier.classify(result)``
turns a raw ``CommandResult`` into a ``NormalizedFailure`` by
pattern-matching on stderr/stdout.

Pattern order matters \u2014 more specific patterns come first:

1. COLLECTION_ERROR \u2014 ModuleNotFoundError / ImportError / "ERROR collecting"
2. TIMEOUT \u2014 "TimeoutExpired" or exit code 124/137
3. INFRASTRUCTURE \u2014 ConnectionError / OSError / PermissionError
4. ASSERTION \u2014 AssertionError or "assert" in stderr/stdout
5. GENERIC_NONZERO \u2014 fallback for any other non-zero exit
"""

from __future__ import annotations

from seharness.validation.runner import (
    CommandResult,
    FailureKind,
    NormalizedFailure,
)


class ClassificationError(ValueError):
    """Raised when the classifier is asked to classify a passing result."""

    def __init__(self, command: str) -> None:
        super().__init__(f"cannot classify passing command: {command!r}")
        self.command = command


# Pattern lists \u2014 order matters.
_COLLECTION_ERROR_PATTERNS = (
    "ModuleNotFoundError",
    "ImportError",
    "ERROR collecting",
    "collection error",
)

_TIMEOUT_PATTERNS = (
    "TimeoutExpired",
    "command timed out",
)

_INFRASTRUCTURE_PATTERNS = (
    "ConnectionError",
    "OSError",
    "PermissionError",
    "DiskQuotaExceeded",
)

_ASSERTION_PATTERNS = (
    "AssertionError",
    "assert ",
)

# Exit codes that strongly suggest timeout.
_TIMEOUT_EXIT_CODES = frozenset({124, 137, 143})


class FailureClassifier:
    """Pure function: ``CommandResult`` \u2192 ``NormalizedFailure``.

    Deterministic; same input always produces same output. Side-effect-free.
    """

    def classify(self, result: CommandResult) -> NormalizedFailure:
        if result.exit_code == 0:
            raise ClassificationError(result.command)

        # Combine both streams for pattern matching; record which stream
        # the first hit came from.
        combined = (result.stderr or "") + "\n" + (result.stdout or "")

        for kind, patterns in (
            (FailureKind.COLLECTION_ERROR, _COLLECTION_ERROR_PATTERNS),
            (FailureKind.INFRASTRUCTURE, _INFRASTRUCTURE_PATTERNS),
            (FailureKind.TIMEOUT, _TIMEOUT_PATTERNS),
        ):
            for pat in patterns:
                if pat in combined:
                    source = "stderr" if pat in (result.stderr or "") else "stdout"
                    return NormalizedFailure(
                        kind=kind,
                        exit_code=result.exit_code,
                        command=result.command,
                        message=_first_line(combined, pat),
                        source=source,
                        duration_s=result.duration_s,
                    )

        # Exit code-only timeout signals.
        if result.exit_code in _TIMEOUT_EXIT_CODES:
            return NormalizedFailure(
                kind=FailureKind.TIMEOUT,
                exit_code=result.exit_code,
                command=result.command,
                message=f"process exited with code {result.exit_code} (timeout signal)",
                source="stderr",
                duration_s=result.duration_s,
            )

        for pat in _ASSERTION_PATTERNS:
            if pat in combined:
                source = "stderr" if pat in (result.stderr or "") else "stdout"
                return NormalizedFailure(
                    kind=FailureKind.ASSERTION,
                    exit_code=result.exit_code,
                    command=result.command,
                    message=_first_line(combined, pat),
                    source=source,
                    duration_s=result.duration_s,
                )

        return NormalizedFailure(
            kind=FailureKind.GENERIC_NONZERO,
            exit_code=result.exit_code,
            command=result.command,
            message=_first_line(combined, ""),
            source="stderr" if (result.stderr or "").strip() else "stdout",
            duration_s=result.duration_s,
        )


def _first_line(combined: str, pattern: str) -> str:
    """Return the first line of ``combined`` that contains ``pattern``,
    or the first non-empty line if ``pattern`` is empty.
    """
    for line in combined.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not pattern or pattern in stripped:
            return stripped
    return combined.splitlines()[0] if combined.splitlines() else ""


__all__ = ["ClassificationError", "FailureClassifier"]
