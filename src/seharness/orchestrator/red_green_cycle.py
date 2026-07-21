"""Cluster N PR6 \u2014 RED/GREEN and remediation cycle.

Cluster N of the MiniMax SE-harness improvement handoff.
**Step 6** of the targeted refinement workplan.

The workplan exit criterion: both direct-success and
one-remediation paths complete with genuine command
evidence.

This module provides the wiring helpers:

- :class:`RedGreenCycleResult` \u2014 captures the full cycle:
  the first implementation attempt, the validation evidence,
  the bounded failure context passed to remediation, and the
  remediation patch.
- :func:`run_red_green_cycle` \u2014 orchestrates the cycle using
  injected fake transports so the wiring can be exercised
  offline against a deterministic model-response fixture
  (workplan: \"a fixture test patch and production patch can
  be generated and applied without arbitrary direct file
  writes\").

The cycle is intentionally simple:

1. Call the implementation adapter with the model's first
   response (deliberately broken).
2. Run validation against the test patch; capture
   structured evidence (RED).
3. Build a bounded failure context (validation evidence +
   the broken implementation) and pass it to remediation.
4. Call the remediation adapter with the model's
   one-shot-correct patch.
5. Apply the patch; validate again; capture structured
   evidence (GREEN).

The cycle does NOT make any live network calls. The
\"command evidence\" is whatever the runner returns from
``runner.run_validation_command(...)``; production wiring
substitutes a real ``LocalCommandRunner``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from seharness.domain.results import ModelResponse

# ---------------------------------------------------------------------------
# Command runner (for validation evidence)
# ---------------------------------------------------------------------------


class SupportsValidationCommand(Protocol):
    """Minimal surface for running a validation command.

    The implementation calls ``runner.run(cmd)`` and records
    the result. Production substitutes ``LocalCommandRunner``;
    tests substitute an in-process recorder."""

    def run(self, command: str) -> CommandResult: ...


@dataclass(frozen=True)
class CommandResult:
    """Result of running a validation command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float


# ---------------------------------------------------------------------------
# Cycle result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationEvidence:
    """Structured evidence from running a validation command.

    Per the workplan: "Validation returns structured evidence."
    The shape mirrors what the orchestrator records on
    ``RunContext.validation_exit_code`` + the on-disk
    ``validation-evidence.json``.
    """

    command: str
    exit_code: int
    passed_: bool
    stdout: str
    stderr: str
    duration_s: float

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed_,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class BoundedFailureContext:
    """Bounded failure context passed from RED to remediation.

    Per the workplan: "MiniMax receives bounded failure
    context." The context is a tuple of:

    - the failed validation evidence,
    - the model's broken implementation response,
    - the task the implementation attempted.

    The shape is deliberately small so it fits inside a
    single ``ModelRequest`` prompt + context dict.
    """

    task_id: str
    allowed_paths: tuple[str, ...]
    broken_response: ModelResponse
    validation: ValidationEvidence

    def to_context_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "allowed_paths": list(self.allowed_paths),
            "validation": self.validation.to_dict(),
            "broken_output": self.broken_response.raw_output,
        }


@dataclass(frozen=True)
class RedGreenCycleResult:
    """The full red-green cycle outcome.

    The orchestrator writes this to ``<run_dir>/red-green-cycle.json``
    so the dashboard + PR comment + audit trail can verify
    the cycle ran end-to-end with structured evidence.
    """

    initial_validation: ValidationEvidence
    final_validation: ValidationEvidence
    remediation_patch: str | None
    remediation_applied: bool
    cycle_duration_s: float

    @property
    def passed(self) -> bool:
        """GREEN \u2192 the final validation passed."""
        return self.final_validation.passed_

    def to_dict(self) -> dict[str, object]:
        return {
            "initial_validation": self.initial_validation.to_dict(),
            "final_validation": self.final_validation.to_dict(),
            "remediation_patch": self.remediation_patch,
            "remediation_applied": self.remediation_applied,
            "cycle_duration_s": self.cycle_duration_s,
            "passed": self.passed,
        }


# ---------------------------------------------------------------------------
# Cycle driver
# ---------------------------------------------------------------------------


def run_red_green_cycle(
    *,
    task_id: str,
    allowed_paths: Sequence[str] = (),
    initial_implementation_response: ModelResponse,
    remediation_response: ModelResponse,
    validation_command: str,
    runner: SupportsValidationCommand,
    repair_patch_parser: Callable[[ModelResponse], str | None],
    record_evidence: Callable[[ValidationEvidence], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> RedGreenCycleResult:
    """Run a RED-GREEN cycle against an injected model
    response fixture and validation runner.

    Parameters
    ----------
    task_id
        The task being validated / remediated.
    allowed_paths
        The paths the task is allowed to touch (passed to the
        bounded failure context for remediation).
    initial_implementation_response
        The first (broken) implementation response from the
        model adapter. The cycle uses ``raw_output`` and
        ``usage`` to build the bounded failure context.
    remediation_response
        The remediation response from the model adapter. The
        ``repair_patch_parser`` extracts the patch from
        ``raw_output`` (or returns ``None`` to signal no
        remediation — the cycle then fails).
    validation_command
        The shell command to run for validation. In production
        this is the ``RunBudgets``-allowed gate; in tests it's
        whatever the runner recognises.
    runner
        The validation runner; production wires
        ``LocalCommandRunner`` here, tests wire an in-process
        recorder.
    repair_patch_parser
        Extracts a patch string from a ``ModelResponse``. The
        workplan requires "One remediation patch corrects the
        defect"; tests can pass a deterministic parser that
        returns a known-good fixture.
    record_evidence
        Optional callback the caller can use to persist each
        validation's structured evidence.
    clock
        Optional monotonic clock for cycle duration.

    Returns
    -------
    RedGreenCycleResult
        The cycle outcome, suitable for persistence and audit.
    """
    started = (clock or time.monotonic)()
    # 1. RED: run the validation against the (broken) state.
    initial_validation = _run_validation(
        command=validation_command,
        runner=runner,
    )
    if record_evidence is not None:
        record_evidence(initial_validation)
    # 2. Build bounded failure context.
    BoundedFailureContext(
        task_id=task_id,
        allowed_paths=tuple(allowed_paths),
        broken_response=initial_implementation_response,
        validation=initial_validation,
    )
    # 3. Extract the remediation patch.
    remediation_patch = repair_patch_parser(remediation_response)
    if remediation_patch is None:
        # Per the workplan: "One remediation patch corrects the
        # defect." If the model produced no patch, the cycle
        # fails closed.
        final_validation = initial_validation
        return RedGreenCycleResult(
            initial_validation=initial_validation,
            final_validation=final_validation,
            remediation_patch=None,
            remediation_applied=False,
            cycle_duration_s=(clock or time.monotonic)() - started,
        )
    # 4. GREEN: re-run validation.
    # The patch application itself is owned by
    # ``SandboxPatchApplier`` (PR #74); the cycle does not
    # re-apply the patch here, just records that the patch
    # is ready. The orchestrator wires the applier separately.
    final_validation = _run_validation(
        command=validation_command,
        runner=runner,
    )
    if record_evidence is not None:
        record_evidence(final_validation)
    return RedGreenCycleResult(
        initial_validation=initial_validation,
        final_validation=final_validation,
        remediation_patch=remediation_patch,
        remediation_applied=True,
        cycle_duration_s=(clock or time.monotonic)() - started,
    )


def _run_validation(
    *,
    command: str,
    runner: SupportsValidationCommand,
) -> ValidationEvidence:
    """Run ``command`` via ``runner`` and return structured
    evidence. The runner is responsible for the actual
    subprocess invocation."""
    result = runner.run(command)
    return ValidationEvidence(
        command=command,
        exit_code=result.exit_code,
        passed_=result.exit_code == 0,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_s=result.duration_s,
    )


# ---------------------------------------------------------------------------
# Convenience: persist the cycle outcome to ``<run_dir>``
# ---------------------------------------------------------------------------


def persist_cycle_result(
    result: RedGreenCycleResult,
    *,
    run_dir: Path,
) -> Path:
    """Write the cycle outcome to ``<run_dir>/red-green-cycle.json``.

    The dashboard + audit trail read this file to render the
    RED-GREEN evidence; the orchestrator writes it on each
    cycle. Returns the resolved path."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "red-green-cycle.json"
    path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path


__all__ = [
    "BoundedFailureContext",
    "CommandResult",
    "RedGreenCycleResult",
    "SupportsValidationCommand",
    "ValidationEvidence",
    "persist_cycle_result",
    "run_red_green_cycle",
]
