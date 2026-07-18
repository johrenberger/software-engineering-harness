"""Task completion validation for slice 6.

Per SPEC \u00a7"TDD evidence", a task is complete when ALL of the
following hold:

- RED evidence exists and is well-formed.
- RED failed with ``FailureKind.EXPECTED_FAILURE`` (not collection /
  infrastructure errors).
- GREEN evidence exists and is well-formed.
- GREEN passed (``exit_code == 0``).
- Required regression tests in the GREEN run cover every required test id.
- No production paths were modified before RED was captured (already
  validated by ``detect_pre_red_violations`` at the workspace layer).

The validator is intentionally strict: any miss raises
``CompletionRejection`` carrying a human-readable reason. It does NOT
mutate disk; that is the reverter's job.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from seharness.execution.evidence import (
    FailureKind,
    GreenResult,
    RedResult,
    TaskEvidenceLayout,
)


@dataclass(frozen=True)
class CompletionRejection(Exception):
    """Raised when a task cannot be marked complete.

    Carries the rejection reason (machine-checkable) and a human
    message (for logs / CLI output).
    """

    reason: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.reason}: {self.message}"


@dataclass(frozen=True)
class TaskCompletionValidator:
    """Strict, side-effect-free completion validator."""

    def assert_complete(self, layout: TaskEvidenceLayout) -> None:
        """Raise ``CompletionRejection`` unless ALL gates pass."""
        self._assert_red(layout)
        self._assert_green(layout)
        self._assert_regression_coverage(layout)

    # -- RED gate ----------------------------------------------------------

    def assert_red_only_fails(self, red_dir: Path) -> None:
        """RED-only assertion: red/ exists, is well-formed, and FAILED.

        Used by callers that want to validate RED without yet requiring
        GREEN (e.g. mid-flow inspection).
        """
        self._assert_red_dir_well_formed(red_dir)
        result = self._parse_red(red_dir)
        if result.exit_code == 0:
            raise CompletionRejection(
                reason="red_passed",
                message="RED evidence shows exit_code=0; RED must fail",
            )
        if result.failure_kind is None:
            raise CompletionRejection(
                reason="missing_failure_kind",
                message="RED result.json must include failure_kind",
            )
        if result.failure_kind != FailureKind.EXPECTED_FAILURE:
            raise CompletionRejection(
                reason="unrelated_failure_kind",
                message=(
                    f"RED failure_kind={result.failure_kind.value!r} is not "
                    f"expected_failure; RED must fail for the expected reason"
                ),
            )

    # -- internal ----------------------------------------------------------

    def _assert_red(self, layout: TaskEvidenceLayout) -> None:
        if not layout.red_dir.exists():
            raise CompletionRejection(
                reason="missing_red",
                message=f"RED evidence directory missing: {layout.red_dir}",
            )
        self.assert_red_only_fails(layout.red_dir)

    def _assert_green(self, layout: TaskEvidenceLayout) -> None:
        if not layout.green_dir.exists():
            raise CompletionRejection(
                reason="missing_green",
                message=f"GREEN evidence directory missing: {layout.green_dir}",
            )
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            if not (layout.green_dir / name).exists():
                raise CompletionRejection(
                    reason="incomplete_green",
                    message=f"GREEN evidence missing file: {name}",
                )
        try:
            raw = json.loads((layout.green_dir / "result.json").read_text())
        except json.JSONDecodeError as e:
            raise CompletionRejection(
                reason="malformed_green_result",
                message=f"GREEN result.json is not valid JSON: {e}",
            ) from e
        try:
            green = GreenResult(**raw)
        except ValidationError as e:
            raise CompletionRejection(
                reason="malformed_green_result",
                message=f"GREEN result.json failed validation: {e}",
            ) from e
        if green.exit_code != 0:
            raise CompletionRejection(
                reason="green_did_not_pass",
                message=f"GREEN did not pass; exit_code={green.exit_code}",
            )

    def _assert_regression_coverage(self, layout: TaskEvidenceLayout) -> None:
        """If GREEN declares ``required_tests``, every one must be in ``covered_tests``."""
        raw = json.loads((layout.green_dir / "result.json").read_text())
        green = GreenResult(**raw)
        required = tuple(green.required_tests)
        covered = set(green.covered_tests)
        missing = tuple(t for t in required if t not in covered)
        if missing:
            raise CompletionRejection(
                reason="missing_regression_tests",
                message=(f"required regression tests did not run: {missing!r}"),
            )

    def _assert_red_dir_well_formed(self, red_dir: object) -> None:
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            if not (red_dir / name).exists():  # type: ignore[operator]
                raise CompletionRejection(
                    reason="incomplete_red",
                    message=f"RED evidence missing file: {name}",
                )

    @staticmethod
    def _parse_red(red_dir: object) -> RedResult:
        try:
            raw = json.loads((red_dir / "result.json").read_text())  # type: ignore[operator]
        except json.JSONDecodeError as e:
            raise CompletionRejection(
                reason="malformed_red_result",
                message=f"RED result.json is not valid JSON: {e}",
            ) from e
        try:
            return RedResult(**raw)
        except ValidationError as e:
            raise CompletionRejection(
                reason="malformed_red_result",
                message=f"RED result.json failed validation: {e}",
            ) from e


__all__ = [
    "CompletionRejection",
    "TaskCompletionValidator",
]


_ = Iterable  # silence unused-import warnings on some type checkers
