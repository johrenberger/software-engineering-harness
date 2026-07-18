"""Test weakening detection for slice 7.

Per SPEC \u00a7"Remediation controller" and slice 7 RED bullet 5, the
controller must refuse to accept remediation that weakens an existing
test. Decision: (A2) test body diff vs previous GREEN \u2014 we
compare ``before`` and ``after`` of a test file and detect weakening
patterns.

The ``TestWeakeningDetector`` uses simple line-level diffing (not
full AST diffing \u2014 slice 7 ships the heuristic detector, slice 8/9
can swap in AST-based detection if needed).

A "weakening" is one of:

- DELETED_ASSERTION: an ``assert ...`` line was removed
- SKIP_REPLACES_ASSERTION: ``assert`` replaced by ``pytest.skip``
- TRIVIAL_ASSERTION: ``assert <expression>`` replaced by ``assert True``
- EMPTY_TEST_BODY: the body of a ``def test_*`` is now empty
- WIDENED_EXCEPTION: ``except NarrowError:`` replaced by ``except Exception:``
"""

from __future__ import annotations

import difflib
from enum import StrEnum
from typing import NamedTuple


class WeakeningKind(StrEnum):
    """Closed set of test-weakening patterns."""

    DELETED_ASSERTION = "deleted_assertion"
    SKIP_REPLACES_ASSERTION = "skip_replaces_assertion"
    TRIVIAL_ASSERTION = "trivial_assertion"
    EMPTY_TEST_BODY = "empty_test_body"
    WIDENED_EXCEPTION = "widened_exception"


class Weakening(NamedTuple):
    """One detected weakening in a test diff."""

    path: str
    line_number: int
    kind: WeakeningKind
    detail: str


class TestWeakeningDetector:
    """Heuristic test-body weakening detector.

    Splits both ``before`` and ``after`` into lines, computes the diff,
    and then re-checks each changed region against the weakening
    patterns. The heuristic is line-level; it does NOT require AST
    parsing.
    """

    def detect(
        self,
        *,
        before: str,
        after: str,
        path: str,
    ) -> tuple[Weakening, ...]:
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        weakenings: list[Weakening] = []
        weakenings.extend(self._detect_substitutions(before_lines, after_lines, path))
        weakenings.extend(self._detect_deletions(before_lines, after_lines, path))
        weakenings.extend(self._detect_empty_test_body(before_lines, after_lines, path))
        return tuple(weakenings)

    @staticmethod
    def _detect_substitutions(
        before_lines: list[str],
        after_lines: list[str],
        path: str,
    ) -> list[Weakening]:
        """TRIVIAL_ASSERTION + SKIP_REPLACES_ASSERTION + WIDENED_EXCEPTION."""
        weakenings: list[Weakening] = []
        for before_line, after_line in zip(before_lines, after_lines, strict=False):
            if before_line == after_line:
                continue
            line_no = before_lines.index(before_line) + 1
            if "assert " in before_line and after_line.strip() == "assert True":
                weakenings.append(
                    Weakening(
                        path=path,
                        line_number=line_no,
                        kind=WeakeningKind.TRIVIAL_ASSERTION,
                        detail=f"replaced {before_line!r} with assert True",
                    )
                )
            if "assert " in before_line and "pytest.skip" in after_line:
                weakenings.append(
                    Weakening(
                        path=path,
                        line_number=line_no,
                        kind=WeakeningKind.SKIP_REPLACES_ASSERTION,
                        detail=f"replaced assert with pytest.skip: {after_line!r}",
                    )
                )
            if "except " in before_line and "except Exception" in after_line:
                weakenings.append(
                    Weakening(
                        path=path,
                        line_number=line_no,
                        kind=WeakeningKind.WIDENED_EXCEPTION,
                        detail=f"widened exception: {after_line!r}",
                    )
                )
        return weakenings

    @staticmethod
    def _detect_deletions(
        before_lines: list[str],
        after_lines: list[str],
        path: str,
    ) -> list[Weakening]:
        """DELETED_ASSERTION: an ``assert ...`` line was removed."""
        weakenings: list[Weakening] = []
        removed = TestWeakeningDetector._diff_removed_lines(before_lines, after_lines)
        for line_no, line in removed:
            if line.strip().startswith("assert "):
                weakenings.append(
                    Weakening(
                        path=path,
                        line_number=line_no,
                        kind=WeakeningKind.DELETED_ASSERTION,
                        detail=f"removed line: {line!r}",
                    )
                )
        return weakenings

    @staticmethod
    def _diff_removed_lines(
        before_lines: list[str], after_lines: list[str]
    ) -> list[tuple[int, str]]:
        """Return ``(line_no, line)`` for every line in ``before`` that
        was removed in ``after``.
        """
        sm = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
        removed: list[tuple[int, str]] = []
        for tag, i1, i2, _, _ in sm.get_opcodes():
            if tag != "delete":
                continue
            for i in range(i1, i2):
                removed.append((i + 1, before_lines[i]))
        return removed

    @staticmethod
    def _detect_empty_test_body(  # noqa: PLR0912
        before_lines: list[str],
        after_lines: list[str],
        path: str,
    ) -> list[Weakening]:
        """Find ``def test_*:`` followed by an empty body in after."""
        weakenings: list[Weakening] = []
        for i, line in enumerate(after_lines):
            if not line.lstrip().startswith("def test_"):
                continue
            indent = len(line) - len(line.lstrip())
            body_indent = indent + 4
            # Look at the next line(s) that aren't blank or comments.
            has_body = False
            for next_line in after_lines[i + 1 :]:
                stripped = next_line.strip()
                if not stripped:
                    continue
                if next_line.startswith(" ") or next_line.startswith("\t"):
                    indent_next = len(next_line) - len(next_line.lstrip())
                    if indent_next >= body_indent:
                        has_body = True
                    break
                # Unindented line \u2014 function body ended (or never started).
                break
            if has_body:
                continue
            # Was the body non-empty in ``before``?
            before_had_body = False
            for j, bline in enumerate(before_lines):
                if j <= i or not bline.lstrip().startswith("def test_"):
                    continue
                b_indent = len(bline) - len(bline.lstrip())
                for bnext in before_lines[j + 1 :]:
                    if not bnext.strip():
                        continue
                    if bnext.startswith(" ") or bnext.startswith("\t"):
                        b_next_indent = len(bnext) - len(bnext.lstrip())
                        if b_next_indent >= b_indent + 4:
                            before_had_body = True
                        break
                    break
                if before_had_body:
                    break
            if before_had_body:
                weakenings.append(
                    Weakening(
                        path=path,
                        line_number=i + 1,
                        kind=WeakeningKind.EMPTY_TEST_BODY,
                        detail=f"test function body emptied: {line!r}",
                    )
                )
        return weakenings


__all__ = [
    "TestWeakeningDetector",
    "Weakening",
    "WeakeningKind",
]
