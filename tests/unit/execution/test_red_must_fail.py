"""RED \u2014 Slice 6 bullet 2: RED must fail for the expected reason.

The RED evidence must show that the test failed *for the reason the task
was supposed to fix* \u2014 not for some unrelated infrastructure problem
(missing import, syntax error, collection error, environment failure).

A passing RED test is invalid RED evidence.
A RED test that fails because pytest couldn't import a module is
"RED-failed-for-unrelated-reason" and must be rejected.

The validator distinguishes:
- ``expected_failure`` \u2014 the assertion failed cleanly
- ``unrelated_failure`` \u2014 collection error, import error, etc.
- ``unexpected_pass`` \u2014 the test passed (RED is supposed to fail)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_RED_EXPECTED_FAIL = {
    "phase": "red",
    "exit_code": 1,
    "duration_s": 0.42,
    "test_id": "tests/unit/foo.py::test_thing",
    "command": "pytest tests/unit/foo.py --no-cov -q",
    "failure_kind": "expected_failure",
    "failure_reason": "AssertionError: assert 1 == 2",
}


@pytest.fixture
def red_dir(tmp_path: Path) -> Path:
    red = tmp_path / "red"
    red.mkdir(parents=True)
    (red / "command.txt").write_text("pytest tests/unit/foo.py --no-cov -q\n")
    (red / "stdout.txt").write_text("FAILED tests/unit/foo.py::test_thing\n")
    (red / "stderr.txt").write_text("AssertionError: assert 1 == 2\n")
    (red / "result.json").write_text(json.dumps(_RED_EXPECTED_FAIL) + "\n")
    return red


class TestRedMustFail:
    """Bullet 2: RED evidence must show the test failed."""

    def test_red_with_exit_code_zero_is_rejected(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        # Mutate the result to show passing RED (exit_code=0).
        payload = json.loads((red_dir / "result.json").read_text())
        payload["exit_code"] = 0
        (red_dir / "result.json").write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_red_only_fails(red_dir)
        assert "passed" in str(exc_info.value).lower() or "red" in str(exc_info.value).lower()

    def test_red_with_exit_code_one_accepted(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator

        # No exception \u2014 exit_code=1 is expected.
        TaskCompletionValidator().assert_red_only_fails(red_dir)


class TestRedFailureKind:
    """Bullet 2: RED must fail for the *expected* reason, not unrelated."""

    def test_red_with_unrelated_failure_kind_rejected(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        payload = json.loads((red_dir / "result.json").read_text())
        payload["failure_kind"] = "collection_error"
        payload["failure_reason"] = "ModuleNotFoundError: foo"
        (red_dir / "result.json").write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection) as exc_info:
            TaskCompletionValidator().assert_red_only_fails(red_dir)
        msg = str(exc_info.value).lower()
        assert "unrelated" in msg or "collection" in msg or "failure_kind" in msg

    def test_red_with_expected_failure_kind_accepted(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator

        payload = json.loads((red_dir / "result.json").read_text())
        payload["failure_kind"] = "expected_failure"
        (red_dir / "result.json").write_text(json.dumps(payload) + "\n")
        TaskCompletionValidator().assert_red_only_fails(red_dir)


class TestRedFailureKindEnum:
    """Failure kind is a closed enum; unknown kinds are rejected."""

    def test_unknown_failure_kind_is_rejected(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        payload = json.loads((red_dir / "result.json").read_text())
        payload["failure_kind"] = "vibes"
        (red_dir / "result.json").write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection):
            TaskCompletionValidator().assert_red_only_fails(red_dir)

    def test_missing_failure_kind_is_rejected(self, red_dir: Path) -> None:
        from seharness.execution.completion import TaskCompletionValidator, CompletionRejection

        payload = json.loads((red_dir / "result.json").read_text())
        del payload["failure_kind"]
        (red_dir / "result.json").write_text(json.dumps(payload) + "\n")

        with pytest.raises(CompletionRejection):
            TaskCompletionValidator().assert_red_only_fails(red_dir)


class TestFailureKindValues:
    """The closed FailureKind enum covers the documented buckets."""

    def test_failure_kind_enum_lists_documented_values(self) -> None:
        from seharness.execution.evidence import FailureKind

        names = {k.name for k in FailureKind}
        assert "EXPECTED_FAILURE" in names
        assert "UNRELATED_FAILURE" in names
        assert "COLLECTION_ERROR" in names
        assert "INFRASTRUCTURE_ERROR" in names

    def test_failure_kind_values_are_stable_strings(self) -> None:
        from seharness.execution.evidence import FailureKind

        assert FailureKind.EXPECTED_FAILURE.value == "expected_failure"
        assert FailureKind.UNRELATED_FAILURE.value == "unrelated_failure"
        assert FailureKind.COLLECTION_ERROR.value == "collection_error"
        assert FailureKind.INFRASTRUCTURE_ERROR.value == "infrastructure_error"