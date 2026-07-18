"""RED — Slice 6 (TDD-aware task execution) bullet 1.

A task cannot complete without RED evidence.

Per SPEC \u00a7"Commit policy under TDD" and \u00a7"TDD evidence", every
implementation task produces:

    execution/<task-id>/red/{command,stdout,stderr,result}.json
    execution/<task-id>/green/{command,stdout,stderr,result}.json

The TaskCompletionValidator rejects task completion when RED evidence
is missing, malformed, or incomplete. This is the gate that prevents
"implementation first, test later" work from being marked done.

Layout:
- ``TestRedEvidenceRequired`` \u2014 RED directory missing entirely
- ``TestRedEvidenceIncomplete`` \u2014 RED present but missing a file
- ``TestRedEvidenceMalformed`` \u2014 result.json missing required fields
- ``TestRedEvidenceWithSuccessMarker`` \u2014 RED result says success
  (RED must FAIL per slice 6 bullet 2, tested separately)
- ``TestAcceptsCompleteRedEvidence`` \u2014 happy path baseline

Why this slice: per SPEC \u00a7"Commit policy under TDD", "The run artifacts
must still preserve proof that the test failed before implementation."
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.red


_RED_RESULT_PAYLOAD = {
    "phase": "red",
    "exit_code": 1,
    "duration_s": 0.42,
    "test_id": "tests/unit/execution/test_red_evidence_required.py::test_placeholder",
    "command": "pytest tests/unit/execution/test_red_evidence_required.py -k test_placeholder --no-cov -q",
}


def _write_red(task_dir: Path, *, missing: str | None = None) -> None:
    """Write a fake RED evidence bundle, optionally omitting one file."""
    red = task_dir / "red"
    red.mkdir(parents=True, exist_ok=True)
    if missing != "command":
        (red / "command.txt").write_text(
            "pytest tests/unit/execution/test_red_evidence_required.py -k test_placeholder --no-cov -q\n"
        )
    if missing != "stdout":
        (red / "stdout.txt").write_text("FAILED tests/unit/...\n")
    if missing != "stderr":
        (red / "stderr.txt").write_text("AssertionError: RED test must fail first\n")
    if missing != "result":
        (red / "result.json").write_text(json.dumps(_RED_RESULT_PAYLOAD) + "\n")


class TestRedEvidenceRequired:
    """Bullet 1: no red/ directory means the task cannot be marked complete."""

    def test_red_directory_missing_rejects_completion(self, tmp_path: Path) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        layout = TaskEvidenceLayout(task_id="T-001", root=tmp_path)
        validator = TaskCompletionValidator()

        with pytest.raises(CompletionRejection) as exc_info:
            validator.assert_complete(layout)
        assert "red" in str(exc_info.value).lower()

    def test_red_directory_with_only_one_file_rejects_completion(self, tmp_path: Path) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        layout = TaskEvidenceLayout(task_id="T-002", root=tmp_path)
        # Only command.txt present; stdout/stderr/result.json absent.
        (layout.task_dir / "red").mkdir(parents=True)
        (layout.task_dir / "red" / "command.txt").write_text("echo hi\n")

        with pytest.raises(CompletionRejection) as exc_info:
            validator = TaskCompletionValidator()
            validator.assert_complete(layout)
        assert "stdout" in str(exc_info.value).lower() or "red" in str(exc_info.value).lower()


class TestRedEvidenceIncomplete:
    """Bullet 1: each of command/stdout/stderr/result is required."""

    @pytest.mark.parametrize("missing", ["command", "stdout", "stderr", "result"])
    def test_each_missing_file_rejects_completion(
        self, tmp_path: Path, missing: str
    ) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        layout = TaskEvidenceLayout(task_id="T-003", root=tmp_path)
        _write_red(layout.task_dir, missing=missing)

        with pytest.raises(CompletionRejection):
            TaskCompletionValidator().assert_complete(layout)


class TestRedEvidenceMalformed:
    """Bullet 1: result.json must be valid JSON with required fields."""

    def test_result_json_invalid_json_rejects_completion(self, tmp_path: Path) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        layout = TaskEvidenceLayout(task_id="T-004", root=tmp_path)
        _write_red(layout.task_dir)
        (layout.task_dir / "red" / "result.json").write_text("not json {{{{")

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_complete(layout)
        assert "result" in str(exc_info.value).lower()

    def test_result_json_missing_required_field_rejects_completion(
        self, tmp_path: Path
    ) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        layout = TaskEvidenceLayout(task_id="T-005", root=tmp_path)
        _write_red(layout.task_dir)
        # Drop the "exit_code" required field
        bad = {k: v for k, v in _RED_RESULT_PAYLOAD.items() if k != "exit_code"}
        (layout.task_dir / "red" / "result.json").write_text(json.dumps(bad) + "\n")

        with pytest.raises(CompletionRejection):
            TaskCompletionValidator().assert_complete(layout)


class TestAcceptsCompleteRedEvidence:
    """Bullet 1 sanity: complete RED evidence passes the gate (then GREEN check fires)."""

    def test_complete_red_with_failing_exit_passes_red_gate(self, tmp_path: Path) -> None:
        from seharness.execution.evidence import TaskEvidenceLayout
        from seharness.execution.completion import TaskCompletionValidator

        layout = TaskEvidenceLayout(task_id="T-006", root=tmp_path)
        _write_red(layout.task_dir)

        # RED gate passes; expect_completion_rejected for *missing* GREEN,
        # not for malformed RED.
        validator = TaskCompletionValidator()
        with pytest.raises(Exception) as exc_info:
            validator.assert_complete(layout)
        # The rejection reason must mention GREEN missing, not RED format.
        assert "green" in str(exc_info.value).lower()


def test_placeholder() -> None:
    """Intentionally failing placeholder test, used to exercise the
    RED recording path during slice-6 development.

    This is the kind of test that ships under execution/<task-id>/red
    before the implementation lands.
    """
    assert False, "RED placeholder must fail until the implementation lands"