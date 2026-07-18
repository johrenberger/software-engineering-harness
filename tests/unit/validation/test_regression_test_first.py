"""RED \u2014 Slice 7 bullet 3: regression defects require a failing test first.

Per SPEC \u00a7"Remediation controller" and slice 7 RED bullet 3, the
controller must refuse to fix a regression defect unless the
``regression_test`` argument is a failing test that:

- exists as a file under the allowed paths
- currently fails when invoked (or is a brand-new test file that was
  never run before, but the controller treats "never run" as "missing
  regression evidence")

The ``RegressionTestRequired`` exception is raised when:
- no regression test is provided
- the provided regression test does not currently exist
- the provided regression test exists but PASSES (no regression to fix)
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestRegressionTestRequired:
    """The remediation controller rejects calls without a regression test."""

    def test_no_regression_test_raises(self) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RegressionTestRequired,
            RemediationController,
        )

        controller = RemediationController(
            allowed_paths=("src/",),
            runner=lambda cmd, evidence: None,  # type: ignore[arg-type]
        )
        with pytest.raises(RegressionTestRequired):
            controller.request_fix(regression_test=None)  # type: ignore[arg-type]

    def test_regression_test_path_must_be_in_allowed_paths(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RegressionTestRequired,
            RemediationController,
        )

        controller = RemediationController(
            allowed_paths=("src/",),
            runner=lambda cmd, evidence: None,  # type: ignore[arg-type]
        )
        # test lives outside allowed paths
        with pytest.raises(RegressionTestRequired):
            controller.request_fix(regression_test="tests/unit/foo.py")


class TestRegressionTestMustFail:
    """The regression test must currently FAIL."""

    def test_passing_regression_test_rejected(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RegressionTestNotFailing,
            RemediationController,
        )

        def fake_runner(cmd: str, evidence: object) -> object:
            # Return a passing command result
            from seharness.validation.runner import CommandResult  # noqa: PLC0415

            return CommandResult(
                command=cmd,
                exit_code=0,
                stdout="1 passed\n",
                stderr="",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/",),
            runner=fake_runner,
        )
        with pytest.raises(RegressionTestNotFailing):
            controller.request_fix(
                regression_test="tests/unit/test_thing.py::test_regression",
            )


class TestRegressionTestAccepted:
    """A failing regression test is accepted."""

    def test_failing_regression_test_accepted(self) -> None:
        from seharness.validation.remediation import RemediationController  # noqa: PLC0415
        from seharness.validation.retry import RetriesExhausted  # noqa: PLC0415
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            return CommandResult(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr="AssertionError: regression\n",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/",),
            runner=fake_runner,
        )
        # Test is currently failing, so validation passes; the
        # retry budget then runs and exhausts. The point of this
        # test is: validation did NOT raise RegressionTestRequired.
        with pytest.raises(RetriesExhausted):
            controller.request_fix(
                regression_test="tests/unit/test_thing.py::test_regression",
            )


class TestRegressionTestPathValidation:
    """The regression test path must be well-formed."""

    def test_relative_path_accepted(self) -> None:
        from seharness.validation.remediation import RemediationController  # noqa: PLC0415
        from seharness.validation.retry import RetriesExhausted  # noqa: PLC0415
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            return CommandResult(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr="AssertionError\n",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/",),
            runner=fake_runner,
        )
        # Validation passes (path is in allowed_paths); retries
        # then exhaust.
        with pytest.raises(RetriesExhausted):
            controller.request_fix(
                regression_test="tests/unit/test_thing.py::test_regression",
            )

    def test_empty_string_rejected(self) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RegressionTestRequired,
            RemediationController,
        )

        controller = RemediationController(
            allowed_paths=("tests/",),
            runner=lambda cmd, evidence: None,  # type: ignore[arg-type]
        )
        with pytest.raises(RegressionTestRequired):
            controller.request_fix(regression_test="")
