"""RED \u2014 Slice 7 bullet 1: failed commands create normalized failures.

Per SPEC \u00a7"Validation runner" and slice 7 RED bullet 1, every failed
command produces a ``NormalizedFailure`` \u2014 a structured record with
stable fields (kind, exit_code, duration, command, message, source).
Callers don't read raw stderr; they consume the normalized form.

The ``CommandResult`` dataclass captures a command's raw outcome; the
``FailureClassifier`` turns it into a ``NormalizedFailure``. The
classifier distinguishes:
- assertion failures
- import / collection errors
- timeout
- infrastructure errors (network, filesystem)
- generic exit-code non-zero
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# CommandResult shape
# ---------------------------------------------------------------------------


class TestCommandResultShape:
    """``CommandResult`` is the input to the failure classifier."""

    def test_command_result_carries_required_fields(self) -> None:
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        r = CommandResult(
            command="pytest tests/unit/foo.py --no-cov -q",
            exit_code=1,
            stdout="FAILED tests/unit/foo.py::test_thing\n",
            stderr="",
            duration_s=0.42,
        )
        assert r.command == "pytest tests/unit/foo.py --no-cov -q"
        assert r.exit_code == 1
        assert r.stdout.startswith("FAILED")
        assert r.stderr == ""
        assert r.duration_s == 0.42


# ---------------------------------------------------------------------------
# NormalizedFailure shape
# ---------------------------------------------------------------------------


class TestNormalizedFailureShape:
    """``NormalizedFailure`` carries stable fields."""

    def test_normalized_failure_carries_required_fields(self) -> None:
        from seharness.validation.runner import FailureKind, NormalizedFailure  # noqa: PLC0415

        nf = NormalizedFailure(
            kind=FailureKind.ASSERTION,
            exit_code=1,
            command="pytest t",
            message="assert 1 == 2",
            source="stderr",
            duration_s=0.42,
        )
        assert nf.kind == FailureKind.ASSERTION
        assert nf.exit_code == 1
        assert nf.command == "pytest t"
        assert nf.message == "assert 1 == 2"
        assert nf.source == "stderr"
        assert nf.duration_s == 0.42


class TestFailureKindEnum:
    """``FailureKind`` is a closed StrEnum."""

    def test_failure_kind_values_are_stable_strings(self) -> None:
        from seharness.validation.runner import FailureKind  # noqa: PLC0415

        assert FailureKind.ASSERTION.value == "assertion"
        assert FailureKind.COLLECTION_ERROR.value == "collection_error"
        assert FailureKind.TIMEOUT.value == "timeout"
        assert FailureKind.INFRASTRUCTURE.value == "infrastructure"
        assert FailureKind.GENERIC_NONZERO.value == "generic_nonzero"

    def test_failure_kind_is_str_enum(self) -> None:
        from seharness.validation.runner import FailureKind  # noqa: PLC0415

        # StrEnum: equality with the underlying string is true.
        assert FailureKind.ASSERTION == "assertion"
        assert isinstance(FailureKind.ASSERTION, str)
        # str() returns the underlying value.
        assert str(FailureKind.TIMEOUT) == "timeout"


# ---------------------------------------------------------------------------
# FailureClassifier behaviour
# ---------------------------------------------------------------------------


class TestAssertionClassification:
    """Assertion failures \u2192 ``ASSERTION``."""

    def test_assertion_in_stderr(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=1,
            stdout="FAILED tests/unit/foo.py::test_x\n",
            stderr="AssertionError: assert 1 == 2\n",
            duration_s=0.42,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.ASSERTION
        assert "AssertionError" in nf.message or "assert" in nf.message.lower()


class TestCollectionErrorClassification:
    """Import / collection errors \u2192 ``COLLECTION_ERROR``."""

    def test_import_error_in_stderr(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=2,
            stdout="",
            stderr="ModuleNotFoundError: No module named 'foo'\n",
            duration_s=0.1,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.COLLECTION_ERROR

    def test_collection_error_in_stdout(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=2,
            stdout="ERROR collecting tests/unit/foo.py\n",
            stderr="",
            duration_s=0.1,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.COLLECTION_ERROR


class TestTimeoutClassification:
    """Timeouts \u2192 ``TIMEOUT``."""

    def test_timeout_marker_in_output(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=124,
            stdout="",
            stderr="TimeoutExpired: command timed out after 30s\n",
            duration_s=30.0,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.TIMEOUT


class TestInfrastructureClassification:
    """Network / filesystem / env errors \u2192 ``INFRASTRUCTURE``."""

    @pytest.mark.parametrize(
        "stderr_fragment",
        [
            "ConnectionError: Failed to establish connection",
            "OSError: [Errno 28] No space left on device",
            "PermissionError: [Errno 13] Permission denied",
        ],
    )
    def test_infrastructure_patterns(self, stderr_fragment: str) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=1,
            stdout="",
            stderr=stderr_fragment + "\n",
            duration_s=0.1,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.INFRASTRUCTURE


class TestGenericNonZeroClassification:
    """Anything else with non-zero exit \u2192 ``GENERIC_NONZERO``."""

    def test_unknown_failure_pattern(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult, FailureKind  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=3,
            stdout="some unknown output\n",
            stderr="some unknown stderr\n",
            duration_s=0.1,
        )
        nf = FailureClassifier().classify(result)
        assert nf.kind == FailureKind.GENERIC_NONZERO


class TestSuccessIsNotAFailure:
    """Exit 0 is not a failure (classifier raises or returns None)."""

    def test_exit_zero_raises(self) -> None:
        from seharness.validation.classifier import (  # noqa: PLC0415
            ClassificationError,
            FailureClassifier,
        )
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=0,
            stdout="1 passed\n",
            stderr="",
            duration_s=0.1,
        )
        with pytest.raises(ClassificationError):
            FailureClassifier().classify(result)


class TestClassifierDeterministic:
    """Same input \u2192 same NormalizedFailure (determinism)."""

    def test_same_input_same_output(self) -> None:
        from seharness.validation.classifier import FailureClassifier  # noqa: PLC0415
        from seharness.validation.runner import CommandResult  # noqa: PLC0415

        result = CommandResult(
            command="pytest t",
            exit_code=1,
            stdout="",
            stderr="AssertionError: x\n",
            duration_s=0.42,
        )
        nf1 = FailureClassifier().classify(result)
        nf2 = FailureClassifier().classify(result)
        assert nf1 == nf2
