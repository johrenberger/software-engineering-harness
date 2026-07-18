"""Mutation killers \u2014 Slice 7 Pydantic config killers + invariants.

Per accumulated slice-4/5/6 lessons, every Pydantic model in slice 7
must defend against:

1. ``ConfigDict(extra=\"forbid\")`` \u2014 catch typos / stray keys.
2. ``frozen=True`` \u2014 assignment after construction must raise.
3. ``validate_assignment=True`` \u2014 mutating an existing attribute
   must re-validate (and reject).
4. Default-value mutations (``None`` \u2192 ``\"\"``) \u2014 tests must OMIT
   the field, not pass ``None``.
5. ``Field(ge=0)`` / ``Field(le=1)`` boundary mutations \u2014 tests must
   include the boundary value.

This file is the slice-7 mutation-killers. It exercises every model
shipped in slice 7.
"""

from __future__ import annotations

import pytest


class TestCommandResultKillers:
    """``CommandResult`` is frozen."""

    def test_command_result_is_frozen(self) -> None:
        from seharness.validation.runner import CommandResult

        r = CommandResult(
            command="pytest t", exit_code=1, stdout="", stderr="",
            duration_s=0.42,
        )
        with pytest.raises(Exception):  # noqa: B017
            r.exit_code = 0  # type: ignore[misc]

    def test_command_result_rejects_extra_field(self) -> None:
        from seharness.validation.runner import CommandResult

        with pytest.raises(Exception):  # noqa: B017
            CommandResult(
                command="pytest t", exit_code=1, stdout="", stderr="",
                duration_s=0.42, not_a_field="oops",
            )


class TestNormalizedFailureKillers:
    """``NormalizedFailure`` is frozen + has closeable ``kind`` enum."""

    def test_normalized_failure_is_frozen(self) -> None:
        from seharness.validation.runner import NormalizedFailure, FailureKind

        nf = NormalizedFailure(
            kind=FailureKind.ASSERTION, exit_code=1, command="t",
            message="m", source="stderr", duration_s=0.42,
        )
        with pytest.raises(Exception):  # noqa: B017
            nf.exit_code = 0  # type: ignore[misc]

    def test_normalized_failure_rejects_unknown_kind(self) -> None:
        from seharness.validation.runner import NormalizedFailure

        with pytest.raises(Exception):  # noqa: B017
            NormalizedFailure(
                kind="vibes",  # type: ignore[arg-type]
                exit_code=1, command="t", message="m", source="stderr",
                duration_s=0.42,
            )

    def test_normalized_failure_accepts_zero_duration(self) -> None:
        """Boundary value: ``ge=0`` allows 0. Mutation ``ge=0\u2192ge=1``
        must be killed here."""
        from seharness.validation.runner import NormalizedFailure, FailureKind

        nf = NormalizedFailure(
            kind=FailureKind.ASSERTION, exit_code=1, command="t",
            message="m", source="stderr", duration_s=0,
        )
        assert nf.duration_s == 0

    def test_normalized_failure_rejects_negative_duration(self) -> None:
        from seharness.validation.runner import NormalizedFailure, FailureKind

        with pytest.raises(Exception):  # noqa: B017
            NormalizedFailure(
                kind=FailureKind.ASSERTION, exit_code=1, command="t",
                message="m", source="stderr", duration_s=-1.0,
            )


class TestBoundedEvidenceKillers:
    """``BoundedEvidence`` is frozen + extra-forbid."""

    def test_bounded_evidence_is_frozen(self) -> None:
        from seharness.validation.remediation import BoundedEvidence

        env = BoundedEvidence(
            failure=None,  # type: ignore[arg-type]
            relevant_files=(),
            previous_green=None,
            allowed_paths=("src/",),
        )
        with pytest.raises(Exception):  # noqa: B017
            env.allowed_paths = ()  # type: ignore[misc]

    def test_bounded_evidence_rejects_extra_field(self) -> None:
        from seharness.validation.remediation import BoundedEvidence

        with pytest.raises(Exception):  # noqa: B017
            BoundedEvidence(
                failure=None,  # type: ignore[arg-type]
                relevant_files=(),
                previous_green=None,
                allowed_paths=("src/",),
                surprise=True,
            )


class TestRemediationResultKillers:
    """``RemediationResult`` invariants."""

    def test_remediation_result_rejects_extra_field(self) -> None:
        from seharness.validation.remediation import RemediationResult

        with pytest.raises(Exception):  # noqa: B017
            RemediationResult(
                regression_test="t", attempts_made=1, exhausted=False,
                bounded_evidence=None,  # type: ignore[arg-type]
                last_command_result=None,  # type: ignore[arg-type]
                surprise=True,
            )


class TestWeakeningKillers:
    """``Weakening`` record + ``WeakeningKind`` invariants."""

    def test_weakening_kind_enum_is_closed(self) -> None:
        from seharness.validation.weakening import WeakeningKind

        # String values that aren't documented must NOT auto-coerce.
        with pytest.raises(Exception):  # noqa: B017
            WeakeningKind("made_up_kind")

    def test_weakening_carries_line_number(self) -> None:
        from seharness.validation.weakening import (
            TestWeakeningDetector,
            Weakening,
            WeakeningKind,
        )

        before = "def test_x() -> None:\n    assert a == 1\n"
        after = "def test_x() -> None:\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert weakenings
        for w in weakenings:
            assert isinstance(w, Weakening)
            assert w.line_number > 0
            assert w.kind != WeakeningKind.DELETED_ASSERTION or w.line_number == 2