"""RED \u2014 Slice 6 bullet 4: GREEN must pass.

Per SPEC \u00a7"TDD evidence": "GREEN evidence is missing" or "GREEN did not
pass" must reject task completion.

Layout:
- ``TestGreenEvidenceRequired`` \u2014 green/ directory missing
- ``TestGreenMustPass`` \u2014 green/ result.json shows non-zero exit
- ``TestGreenAcceptsPass`` \u2014 happy path
- ``TestGreenRegressionCoverage`` \u2014 regression tests must have
  actually run (per SPEC: "required regression tests did not run")
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_GREEN_RESULT = {
    "phase": "green",
    "exit_code": 0,
    "duration_s": 1.23,
    "test_id": "tests/unit/foo.py::test_thing",
    "command": "pytest tests/unit/foo.py --no-cov -q",
}


def _write_green(task_dir: Path, *, missing: str | None = None) -> None:
    green = task_dir / "green"
    green.mkdir(parents=True, exist_ok=True)
    if missing != "command":
        (green / "command.txt").write_text("pytest tests/unit/foo.py --no-cov -q\n")
    if missing != "stdout":
        (green / "stdout.txt").write_text("1 passed\n")
    if missing != "stderr":
        (green / "stderr.txt").write_text("")
    if missing != "result":
        (green / "result.json").write_text(json.dumps(_GREEN_RESULT) + "\n")


class TestGreenEvidenceRequired:
    """Bullet 4: green/ directory must exist."""

    def test_green_directory_missing_rejects_completion(self, tmp_path: Path) -> None:
        from seharness.execution.completion import (  # noqa: PLC0415
            CompletionRejection,
            TaskCompletionValidator,
        )
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-101", root=tmp_path)
        # Only red/ present.
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "duration_s": 0.1,
                        "test_id": "t",
                        "command": "pytest t",
                        "failure_kind": "expected_failure",
                        "failure_reason": "AssertionError",
                    }
                )
            )

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_complete(layout)
        assert "green" in str(exc_info.value).lower()

    @pytest.mark.parametrize("missing", ["command", "stdout", "stderr", "result"])
    def test_each_missing_green_file_rejects_completion(self, tmp_path: Path, missing: str) -> None:
        from seharness.execution.completion import (  # noqa: PLC0415
            CompletionRejection,
            TaskCompletionValidator,
        )
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-102", root=tmp_path)
        # Also need a complete RED for the validator to get past the RED gate.
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "failure_kind": "expected_failure",
                        "test_id": "t",
                        "command": "pytest t",
                        "duration_s": 0.1,
                    }
                )
            )

        _write_green(layout.task_dir, missing=missing)

        with pytest.raises(CompletionRejection):
            TaskCompletionValidator().assert_complete(layout)


class TestGreenMustPass:
    """Bullet 4: green/ result.json exit_code must be 0."""

    def test_green_with_nonzero_exit_code_rejected(self, tmp_path: Path) -> None:
        from seharness.execution.completion import (  # noqa: PLC0415
            CompletionRejection,
            TaskCompletionValidator,
        )
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-103", root=tmp_path)
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "failure_kind": "expected_failure",
                        "test_id": "t",
                        "command": "pytest t",
                        "duration_s": 0.1,
                    }
                )
            )
        _write_green(layout.task_dir)
        # Mutate exit_code to non-zero.
        result_path = layout.task_dir / "green" / "result.json"
        payload = json.loads(result_path.read_text())
        payload["exit_code"] = 2
        result_path.write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_complete(layout)
        assert "exit_code" in str(exc_info.value) or "did not pass" in str(exc_info.value).lower()


class TestGreenAcceptsPass:
    """Sanity: complete RED + complete passing GREEN \u2192 validator is silent
    (no exception raised)."""

    def test_complete_red_and_green_accepted(self, tmp_path: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator  # noqa: PLC0415
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-104", root=tmp_path)
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "failure_kind": "expected_failure",
                        "test_id": "t",
                        "command": "pytest t",
                        "duration_s": 0.1,
                    }
                )
            )
        _write_green(layout.task_dir)

        TaskCompletionValidator().assert_complete(layout)


class TestGreenRegressionCoverage:
    """Per SPEC: "required regression tests did not run" must reject.

    The validator tracks which required test ids the GREEN run covered
    via the ``covered_tests`` field on the GREEN result.json. Missing
    tests \u2192 rejection.
    """

    def test_missing_covered_test_rejected(self, tmp_path: Path) -> None:
        from seharness.execution.completion import (  # noqa: PLC0415
            CompletionRejection,
            TaskCompletionValidator,
        )
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-105", root=tmp_path)
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "failure_kind": "expected_failure",
                        "test_id": "t",
                        "command": "pytest t",
                        "duration_s": 0.1,
                    }
                )
            )
        _write_green(layout.task_dir)
        result_path = layout.task_dir / "green" / "result.json"
        payload = json.loads(result_path.read_text())
        payload["covered_tests"] = ["tests/unit/foo.py::test_thing"]
        payload["required_tests"] = [
            "tests/unit/foo.py::test_thing",
            "tests/unit/bar.py::test_other",
        ]
        result_path.write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_complete(layout)
        assert (
            "regression" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()
        )

    def test_all_required_tests_covered_accepted(self, tmp_path: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator  # noqa: PLC0415
        from seharness.execution.evidence import TaskEvidenceLayout  # noqa: PLC0415

        layout = TaskEvidenceLayout(task_id="T-106", root=tmp_path)
        red = layout.task_dir / "red"
        red.mkdir(parents=True)
        for name in ("command.txt", "stdout.txt", "stderr.txt", "result.json"):
            (red / name).write_text(
                "x"
                if name.endswith(".txt")
                else json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "failure_kind": "expected_failure",
                        "test_id": "t",
                        "command": "pytest t",
                        "duration_s": 0.1,
                    }
                )
            )
        _write_green(layout.task_dir)
        result_path = layout.task_dir / "green" / "result.json"
        payload = json.loads(result_path.read_text())
        payload["covered_tests"] = ["t1", "t2"]
        payload["required_tests"] = ["t1", "t2"]
        result_path.write_text(json.dumps(payload) + "\n")

        TaskCompletionValidator().assert_complete(layout)
