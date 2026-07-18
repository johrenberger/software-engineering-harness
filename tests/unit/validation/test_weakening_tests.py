"""RED \u2014 Slice 7 bullet 5: weakening tests is detected.

Per SPEC \u00a7"Remediation controller" and slice 7 RED bullet 5, the
controller must refuse to accept remediation that weakens an existing
test. A "weakening" is:

- deletion of any assertion inside ``def test_*(...)``
- deletion of the entire test function body
- changing an assertion from ``assert x == y`` to ``assert True`` or
  ``pytest.skip(...)``
- widening an ``exception`` parameter (``except ValueError`` \u2192
  ``except Exception``)

Decision: (A2) test body diff vs previous GREEN \u2014 the controller
compares the test file BEFORE and AFTER remediation. Any change
that weakens coverage is flagged.

The ``TestWeakeningDetector`` analyses a unified diff between the
pre- and post-remediation test file and returns a list of
``Weakening`` records.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestWeakeningDetector:
    """The detector analyses a diff and flags weakenings."""

    def test_deleted_assertion_flagged(self) -> None:
        from seharness.validation.weakening import (  # noqa: PLC0415
            TestWeakeningDetector,
            WeakeningKind,
        )

        before = "def test_thing() -> None:\n    assert foo() == 1\n    assert bar() == 2\n"
        after = "def test_thing() -> None:\n    assert foo() == 1\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert any(w.kind == WeakeningKind.DELETED_ASSERTION for w in weakenings)

    def test_assertion_loosened_to_skip_flagged(self) -> None:
        from seharness.validation.weakening import (  # noqa: PLC0415
            TestWeakeningDetector,
            WeakeningKind,
        )

        before = "def test_thing() -> None:\n    assert foo() == 1\n"
        after = "def test_thing() -> None:\n    pytest.skip('not relevant')\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert any(w.kind == WeakeningKind.SKIP_REPLACES_ASSERTION for w in weakenings)

    def test_assertion_loosened_to_true_flagged(self) -> None:
        from seharness.validation.weakening import (  # noqa: PLC0415
            TestWeakeningDetector,
            WeakeningKind,
        )

        before = "def test_thing() -> None:\n    assert foo() == 1\n"
        after = "def test_thing() -> None:\n    assert True\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert any(w.kind == WeakeningKind.TRIVIAL_ASSERTION for w in weakenings)

    def test_added_assertion_not_flagged(self) -> None:
        from seharness.validation.weakening import TestWeakeningDetector  # noqa: PLC0415

        before = "def test_thing() -> None:\n    assert foo() == 1\n"
        after = "def test_thing() -> None:\n    assert foo() == 1\n    assert bar() == 2\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert tuple(weakenings) == ()

    def test_unchanged_test_not_flagged(self) -> None:
        from seharness.validation.weakening import TestWeakeningDetector  # noqa: PLC0415

        text = "def test_thing() -> None:\n    assert foo() == 1\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=text, after=text, path="t.py")
        assert tuple(weakenings) == ()


class TestWeakeningKindEnum:
    """``WeakeningKind`` is a closed enum."""

    def test_weakening_kind_values(self) -> None:
        from seharness.validation.weakening import WeakeningKind  # noqa: PLC0415

        assert WeakeningKind.DELETED_ASSERTION.value == "deleted_assertion"
        assert WeakeningKind.SKIP_REPLACES_ASSERTION.value == "skip_replaces_assertion"
        assert WeakeningKind.TRIVIAL_ASSERTION.value == "trivial_assertion"
        assert WeakeningKind.EMPTY_TEST_BODY.value == "empty_test_body"
        assert WeakeningKind.WIDENED_EXCEPTION.value == "widened_exception"


class TestWeakeningShape:
    """``Weakening`` record carries enough info to reject."""

    def test_weakening_carries_path_and_line(self) -> None:
        from seharness.validation.weakening import (  # noqa: PLC0415
            TestWeakeningDetector,
            Weakening,
        )

        before = "def test_x() -> None:\n    assert a == 1\n"
        after = "def test_x() -> None:\n"
        detector = TestWeakeningDetector()
        weakenings = detector.detect(before=before, after=after, path="t.py")
        assert len(weakenings) >= 1
        w = weakenings[0]
        assert isinstance(w, Weakening)
        assert w.path == "t.py"
        assert w.line_number > 0
        assert w.kind.value != ""


class TestRemediationRefusesWeakening:
    """The remediation controller refuses to apply a weakening diff."""

    def test_weakening_diff_rejected(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (  # noqa: PLC0415
            RemediationController,
            WeakeningDetected,
        )
        from seharness.validation.runner import CommandResult  # noqa: PLC0415
        from seharness.validation.weakening import TestWeakeningDetector  # noqa: PLC0415

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            return CommandResult(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr="AssertionError\n",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/", "src/"),
            runner=fake_runner,
            weakening_detector=TestWeakeningDetector(),
        )

        before = "def test_regression() -> None:\n    assert foo() == 1\n    assert foo() == 2\n"
        after = "def test_regression() -> None:\n    assert foo() == 1\n"

        with pytest.raises(WeakeningDetected):
            controller.apply_diff(
                path="tests/unit/test_thing.py",
                before=before,
                after=after,
            )


class TestRemediationAcceptsNonWeakening:
    """A non-weakening diff is accepted."""

    def test_non_weakening_diff_accepted(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import RemediationController  # noqa: PLC0415
        from seharness.validation.runner import CommandResult  # noqa: PLC0415
        from seharness.validation.weakening import TestWeakeningDetector  # noqa: PLC0415

        def fake_runner(cmd: str, evidence: object) -> CommandResult:
            return CommandResult(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr="AssertionError\n",
                duration_s=0.42,
            )

        controller = RemediationController(
            allowed_paths=("tests/", "src/"),
            runner=fake_runner,
            weakening_detector=TestWeakeningDetector(),
        )

        text = "def test_regression() -> None:\n    assert foo() == 1\n    assert foo() == 2\n"

        # No change \u2192 no weakening.
        controller.apply_diff(path="tests/unit/test_thing.py", before=text, after=text)
