"""RED \u2014 Slice 7 remediation controller service boundary.

Per SPEC \u00a7"Remediation controller" and slice 7 GREEN deliverables,
the controller is the public boundary used by slice 9 (orchestrator)
when remediation is needed. It owns:

- the regression test (must fail)
- the bounded evidence envelope
- the retry budget
- the weakening detector

The ``request_fix(regression_test)`` flow:
1. Validate the regression test exists and currently fails.
2. Build a BoundedEvidence envelope (filtered to allowed paths).
3. Run the runner against the regression test (recording retry).
4. Return a ``RemediationResult`` summarizing the attempt.
"""

from __future__ import annotations

from pathlib import Path


class TestRemediationControllerService:
    """``RemediationController.request_fix`` returns a result object."""

    def test_request_fix_eventually_succeeds(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RemediationController,
            RemediationResult,
        )
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        # Runner always fails on the validation probe, then succeeds on
        # the first real attempt.
        calls = {"probe": True}

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            is_probe = calls["probe"]
            calls["probe"] = False
            return CommandResult(
                command=cmd,
                exit_code=1 if is_probe else 0,
                stdout="",
                stderr="AssertionError\n" if is_probe else "",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/", "src/"),
            runner=fake_runner,
        )
        result = controller.request_fix(
            regression_test="tests/unit/test_x.py::test_regression",
        )
        assert isinstance(result, RemediationResult)
        assert result.regression_test == "tests/unit/test_x.py::test_regression"
        assert result.attempts_made == 1
        assert result.exhausted is False

    def test_request_fix_with_retry_then_success(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RemediationController,
            RemediationResult,
        )
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        attempts = {"count": 0}

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            attempts["count"] += 1
            return CommandResult(
                command=cmd,
                exit_code=0 if attempts["count"] >= 2 else 1,
                stdout="",
                stderr="",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/", "src/"),
            runner=fake_runner,
            max_attempts=3,
        )
        result = controller.request_fix(
            regression_test="tests/unit/test_x.py::test_regression",
        )
        assert isinstance(result, RemediationResult)
        # Validation probe failed once; the first real attempt
        # succeeded. So attempts_made == 1, not 2.
        assert result.attempts_made == 1
        assert result.exhausted is False


class TestRemediationResultShape:
    """``RemediationResult`` exposes the documented fields."""

    def test_result_has_required_fields(self) -> None:
        from seharness.validation.remediation import RemediationResult  # noqa: PLC0415

        r = RemediationResult(
            regression_test="t",
            attempts_made=1,
            exhausted=False,
            bounded_evidence=None,  # type: ignore[arg-type]
            last_command_result=None,  # type: ignore[arg-type]
        )
        assert r.regression_test == "t"
        assert r.attempts_made == 1
        assert r.exhausted is False


class TestRemediationUsesBoundedEvidence:
    """The controller passes a ``BoundedEvidence`` to the runner."""

    def test_runner_receives_bounded_evidence(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            BoundedEvidence,
            RemediationController,
        )
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        captured: dict[str, object] = {}

        # First call (validation probe) fails; subsequent calls succeed.
        calls = {"n": 0}

        def fake_runner(cmd: str, evidence: BoundedEvidence) -> CommandResult:
            calls["n"] += 1
            captured["evidence"] = evidence
            return CommandResult(
                command=cmd,
                exit_code=1 if calls["n"] == 1 else 0,
                stdout="",
                stderr="AssertionError\n" if calls["n"] == 1 else "",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/", "src/"),
            runner=fake_runner,  # type: ignore[arg-type]
            repo_root=tmp_path,
        )
        controller.request_fix(
            regression_test="tests/unit/test_x.py::test_regression",
        )
        assert "evidence" in captured
        assert isinstance(captured["evidence"], BoundedEvidence)
