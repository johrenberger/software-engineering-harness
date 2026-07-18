"""Remediation controller and bounded evidence envelope for slice 7.

Per SPEC \u00a7"Remediation controller", the controller:

1. Validates the regression test (must exist, must currently fail).
2. Builds a ``BoundedEvidence`` envelope from the failure + workspace.
3. Runs the runner with retry budget tracking.
4. Refuses to apply a diff that weakens an existing test.

The envelope is the public boundary: callers never see the full repo,
they see only the relevant files + failure + previous green.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from seharness.validation.retry import (
    RetriesExhausted,
    RetryBudget,
)
from seharness.validation.runner import (
    CommandResult,
    FailureKind,
    NormalizedFailure,
)
from seharness.validation.weakening import (
    TestWeakeningDetector,
    Weakening,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RegressionTestRequired(ValueError):
    """Raised when ``request_fix`` is called without a regression test."""


class RegressionTestNotFailing(RuntimeError):
    """Raised when the supplied regression test currently PASSES."""


class WeakeningDetected(RuntimeError):
    """Raised when an attempted remediation diff weakens an existing test."""

    def __init__(self, weakenings: tuple[Weakening, ...]) -> None:
        super().__init__(f"remediation rejected: {len(weakenings)} weakening(s) detected")
        self.weakenings = weakenings


class BoundedEvidenceBuildError(RuntimeError):
    """Raised when the evidence envelope cannot be built."""


# ---------------------------------------------------------------------------
# Evidence envelope
# ---------------------------------------------------------------------------


class RelevantFile(BaseModel):
    """One file included in the ``BoundedEvidence`` envelope.

    ``content_bytes`` is the file content (capped at
    ``max_bytes_per_file``). ``truncated`` indicates whether the
    content was cut off.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    content_bytes: bytes
    truncated: bool


class BoundedEvidence(BaseModel):
    """The bounded evidence envelope passed to remediation runners.

    Public surface: callers may inspect ``failure``,
    ``relevant_files``, ``previous_green``, ``allowed_paths``. The
    full repository state is intentionally NOT exposed — that's
    the whole point of the envelope.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure: NormalizedFailure | None
    relevant_files: tuple[RelevantFile, ...]
    previous_green: NormalizedFailure | None
    allowed_paths: tuple[str, ...]


@dataclass(frozen=True)
class BoundedEvidenceBuilder:
    """Builds a ``BoundedEvidence`` envelope from a failure + repo.

    Filters to ``allowed_paths``, truncates file content, caps total
    payload size. Construction rejects empty ``allowed_paths``.
    """

    repo_root: Path
    allowed_paths: tuple[str, ...]
    max_bytes_per_file: int = 4096
    max_total_bytes: int = 32_768

    def __post_init__(self) -> None:
        if not self.allowed_paths:
            raise ValueError("allowed_paths must be non-empty")

    def build(
        self,
        *,
        failure: NormalizedFailure,
        command_result: CommandResult | None = None,
        previous_green: NormalizedFailure | None = None,
    ) -> BoundedEvidence:
        # Normalize allowed_paths for prefix comparison.
        allowed = tuple(p.rstrip("/") for p in self.allowed_paths)
        files: list[RelevantFile] = []
        total = 0
        if self.repo_root.exists():
            for path in sorted(self.repo_root.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.repo_root).as_posix()
                if not self._is_under_any(rel, allowed):
                    continue
                if any(part.startswith(".") for part in Path(rel).parts):
                    continue
                if any(part == "__pycache__" for part in Path(rel).parts):
                    continue
                try:
                    raw = path.read_bytes()
                except OSError:
                    continue
                truncated = False
                if len(raw) > self.max_bytes_per_file:
                    raw = raw[: self.max_bytes_per_file]
                    truncated = True
                if total + len(raw) > self.max_total_bytes:
                    raw = raw[: max(0, self.max_total_bytes - total)]
                    truncated = True
                total += len(raw)
                files.append(RelevantFile(path=rel, content_bytes=raw, truncated=truncated))
                if total >= self.max_total_bytes:
                    break
        return BoundedEvidence(
            failure=failure,
            relevant_files=tuple(files),
            previous_green=previous_green,
            allowed_paths=self.allowed_paths,
        )

    @staticmethod
    def _is_under_any(rel: str, allowed: tuple[str, ...]) -> bool:
        for prefix in allowed:
            if rel == prefix or rel.startswith(prefix + "/") or rel.startswith(prefix):
                return True
        return False


# ---------------------------------------------------------------------------
# Remediation controller
# ---------------------------------------------------------------------------


class RemediationResult(BaseModel):
    """Public result of a ``request_fix`` call.

    ``attempts_made`` and ``exhausted`` summarise the retry run.
    ``bounded_evidence`` and ``last_command_result`` let the caller
    inspect what was passed to the runner.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    regression_test: str = Field(min_length=1)
    attempts_made: int = Field(ge=0)
    exhausted: bool
    bounded_evidence: BoundedEvidence | None
    last_command_result: CommandResult | None


# Runner protocol: takes command + evidence, returns CommandResult.
# Default impls inject a callable; the controller never invokes the
# regression test directly.
RunnerFunc = Callable[[str, BoundedEvidence], CommandResult]


@dataclass(frozen=True)
class RemediationController:
    """Public boundary: ``request_fix(regression_test)`` flow.

        Construction accepts:
    - ``allowed_paths``: which paths the runner may modify
    - ``runner``: callable ``(command, evidence) -> CommandResult``
    - ``max_attempts``: per-task retry budget (default 3)
    - ``repo_root``: optional; used to build the bounded evidence
    - ``weakening_detector``: optional; used to validate remediation diffs
    """

    allowed_paths: tuple[str, ...]
    runner: RunnerFunc
    max_attempts: int = 3
    repo_root: Path | None = None
    weakening_detector: TestWeakeningDetector | None = None

    def __post_init__(self) -> None:
        if not self.allowed_paths:
            raise ValueError("allowed_paths must be non-empty")
        if self.max_attempts <= 0:
            raise ValueError(f"max_attempts must be > 0, got {self.max_attempts}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_fix(
        self,
        *,
        regression_test: str,
    ) -> RemediationResult:
        """Request a remediation run for ``regression_test``.

        Raises:
            RegressionTestRequired: test is None / empty / outside allowed.
            RegressionTestNotFailing: test currently passes.
            RetriesExhausted: max attempts reached without success.
        """
        # Validation step: separate from the retry budget. We need to
        # verify the test path is valid AND that it currently fails
        # BEFORE allocating the retry budget.
        self._validate_regression_test_path(regression_test)
        self._assert_test_currently_fails(regression_test)

        budget = RetryBudget(task_id=regression_test, max_attempts=self.max_attempts)

        last: CommandResult | None = None
        attempts = 0
        while budget.can_attempt:
            budget.record_attempt()
            attempts += 1
            evidence = self._build_evidence_for(regression_test)
            last = self.runner(regression_test, evidence)
            if last.exit_code == 0:
                break

        if last is None or last.exit_code != 0:
            raise RetriesExhausted(task_id=regression_test, max_attempts=self.max_attempts)

        return RemediationResult(
            regression_test=regression_test,
            attempts_made=attempts,
            exhausted=False,
            bounded_evidence=self._build_evidence_for(regression_test),
            last_command_result=last,
        )

    def apply_diff(
        self,
        *,
        path: str,
        before: str,
        after: str,
    ) -> tuple[Weakening, ...]:
        """Apply (or rather: validate) a remediation diff.

        Refuses to apply any diff that contains a weakening. Returns
        the (empty) tuple of weakenings when accepted. Raises
        ``WeakeningDetected`` otherwise.
        """
        detector = self.weakening_detector or TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path=path)
        if weakenings:
            raise WeakeningDetected(weakenings)
        return ()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validate_regression_test_path(self, test: str | None) -> None:
        """Validate the regression test path itself (allowed / non-empty)."""
        if not test:
            raise RegressionTestRequired("regression_test must be a non-empty string")
        first_segment = test.split("/", 1)[0]
        if first_segment not in self.allowed_paths and not any(
            test.startswith(p) for p in self.allowed_paths
        ):
            raise RegressionTestRequired(
                f"regression_test {test!r} is outside allowed_paths {self.allowed_paths!r}"
            )

    def _assert_test_currently_fails(self, test: str) -> None:
        """Run the regression test once to confirm it currently fails."""
        evidence = self._build_evidence_for(test)
        result = self.runner(test, evidence)
        if result.exit_code == 0:
            raise RegressionTestNotFailing(f"regression_test {test!r} is not currently failing")

    def _build_evidence_for(self, regression_test: str) -> BoundedEvidence:
        """Build a minimal evidence envelope (no previous green in slice 7)."""
        failure = NormalizedFailure(
            kind=FailureKind.GENERIC_NONZERO,
            exit_code=1,
            command=regression_test,
            message="regression test failed",
            source="stderr",
            duration_s=0.0,
        )
        if self.repo_root is None:
            return BoundedEvidence(
                failure=failure,
                relevant_files=(),
                previous_green=None,
                allowed_paths=self.allowed_paths,
            )
        builder = BoundedEvidenceBuilder(
            repo_root=self.repo_root,
            allowed_paths=self.allowed_paths,
        )
        return builder.build(failure=failure)


__all__ = [
    "BoundedEvidence",
    "BoundedEvidenceBuildError",
    "BoundedEvidenceBuilder",
    "RegressionTestNotFailing",
    "RegressionTestRequired",
    "RelevantFile",
    "RemediationController",
    "RemediationResult",
    "RunnerFunc",
    "WeakeningDetected",
]
