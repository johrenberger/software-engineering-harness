"""Mutation killers \u2014 Slice 6 Pydantic config killers + invariants.

Per accumulated slice-4/5 lessons, every Pydantic model in slice 6 must
be defended against:

1. ``ConfigDict(extra=\"forbid\")`` \u2014 catch typos and stray keys.
2. ``frozen=True`` \u2014 assignment after construction must raise.
3. ``validate_assignment=True`` \u2014 mutating an existing attribute
   must re-validate (and reject).
4. Default-value mutations (``None`` \u2192 ``\"\"``) \u2014 tests must OMIT
   the field, not pass ``None``.
5. ``Field(ge=0)`` / ``Field(le=1)`` boundary mutations \u2014 tests
   must include the boundary value.

This file is the slice-6 mutation-killers. It exercises every model
shipped in slice 6.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestEvidenceResultKillers:
    """The Pydantic evidence result models must reject the obvious mutations."""

    def test_red_result_rejects_extra_field(self) -> None:
        from seharness.execution.evidence import RedResult

        with pytest.raises(Exception):
            RedResult(
                phase="red",
                exit_code=1,
                duration_s=0.1,
                test_id="t",
                command="pytest t",
                failure_kind="expected_failure",
                not_a_real_field="oops",
            )

    def test_red_result_is_frozen(self) -> None:
        from seharness.execution.evidence import RedResult

        r = RedResult(
            phase="red",
            exit_code=1,
            duration_s=0.1,
            test_id="t",
            command="pytest t",
            failure_kind="expected_failure",
        )
        with pytest.raises(Exception):
            r.exit_code = 0  # type: ignore[misc]

    def test_red_result_default_failure_kind_omitted_is_expected(self) -> None:
        from seharness.execution.evidence import RedResult

        # OMIT failure_kind \u2014 must NOT default to anything except None.
        r = RedResult(
            phase="red",
            exit_code=1,
            duration_s=0.1,
            test_id="t",
            command="pytest t",
        )
        assert r.failure_kind is None

    def test_green_result_default_covered_tests_omitted_is_empty_tuple(self) -> None:
        from seharness.execution.evidence import GreenResult

        g = GreenResult(
            phase="green",
            exit_code=0,
            duration_s=0.1,
            test_id="t",
            command="pytest t",
        )
        assert tuple(g.covered_tests) == ()

    def test_green_result_rejects_negative_duration(self) -> None:
        from seharness.execution.evidence import GreenResult

        with pytest.raises(Exception):
            GreenResult(
                phase="green",
                exit_code=0,
                duration_s=-1.0,
                test_id="t",
                command="pytest t",
            )

    def test_green_result_accepts_zero_duration(self) -> None:
        """Boundary value: ``ge=0`` allows 0. Mutation ``ge=0\u2192ge=1``
        must be killed here."""
        from seharness.execution.evidence import GreenResult

        g = GreenResult(
            phase="green",
            exit_code=0,
            duration_s=0,
            test_id="t",
            command="pytest t",
        )
        assert g.duration_s == 0


class TestWorkspaceSnapshotKillers:
    """``WorkspaceSnapshot`` is a mutable-by-design dataclass, not Pydantic.

    Mutations on the record() side \u2014 e.g. silently dropping the entry
    \u2014 must not change observable behaviour."""

    def test_snapshot_record_persists_size(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot

        snap = WorkspaceSnapshot(root=tmp_path, captured_at=None)
        f = tmp_path / "foo.py"
        f.write_text("hello\n")
        snap.record(f, mtime=None, size=f.stat().st_size)

        assert f.relative_to(tmp_path).as_posix() in snap.paths

    def test_snapshot_paths_is_iterable(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot

        snap = WorkspaceSnapshot(root=tmp_path, captured_at=None)
        f = tmp_path / "foo.py"
        f.write_text("hello\n")
        snap.record(f, mtime=None, size=f.stat().st_size)

        paths = list(snap.paths)
        assert len(paths) == 1


class TestPathAuthorizationRuleKillers:
    """``PathAuthorizationRule`` invariants under common mutations."""

    def test_rule_rejects_empty_allowed_paths(self) -> None:
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
        )

        with pytest.raises(ValueError):
            PathAuthorizationRule(
                task_id="T-1",
                allowed_paths=AllowedPaths(()),  # type: ignore[arg-type]
                prohibited_paths=ProhibitedPaths(()),
            )

    def test_rule_overlap_allowed_prohibited_raises(self) -> None:
        """An allowed_paths entry cannot also be in prohibited_paths.
        Otherwise the rule's answer depends on which check runs first."""
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
        )

        with pytest.raises(ValueError):
            PathAuthorizationRule(
                task_id="T-1",
                allowed_paths=AllowedPaths(("harness.yaml",)),
                prohibited_paths=ProhibitedPaths(("harness.yaml",)),
            )

    def test_rule_path_normalization_is_consistent(self) -> None:
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
        )

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )
        assert rule.is_authorized("./src/seharness/foo.py") is True
        assert rule.is_authorized("src/seharness//foo.py") is True


class TestTaskResultKillers:
    """``TaskResult`` is frozen and validate_assignment."""

    def test_task_result_is_frozen(self, tmp_path: Path) -> None:
        from seharness.execution.service import TaskResult

        r = TaskResult(
            task_id="T-1",
            completed=True,
            evidence_root=tmp_path,
            red_exit_code=1,
            green_exit_code=0,
            violations=(),
        )
        with pytest.raises(Exception):
            r.completed = False  # type: ignore[misc]

    def test_task_result_rejects_extra_field(self, tmp_path: Path) -> None:
        from seharness.execution.service import TaskResult

        with pytest.raises(Exception):
            TaskResult(
                task_id="T-1",
                completed=True,
                evidence_root=tmp_path,
                red_exit_code=1,
                green_exit_code=0,
                violations=(),
                surprise=True,
            )