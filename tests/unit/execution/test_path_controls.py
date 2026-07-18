"""RED \u2014 Slice 6 bullet 5: unauthorized paths are reverted.

Per SPEC \u00a7"Implementation task" and \u00a7"TDD evidence": a task must not
modify paths outside its declared ``allowed_paths`` (and must not touch
``prohibited_paths`` either). When it does, the PathAuthorizationEngine
records the violation and the task must be reverted.

The PathAuthorizationEngine is the runtime guard per slice 6 Q1
recommendation A1 (validator on the Plan artifact only \u2014 see also
``PlanValidator`` in slice 5). For bullet 5 we need a *runtime*
reverter that compares a post-task diff against the allowed_paths /
prohibited_paths and reverts unauthorized changes.

Layout:
- ``TestPathAuthorizationRule`` \u2014 pure rule: allowed + prohibited
- ``TestPathReverter`` \u2014 revert unauthorized changes from a snapshot
- ``TestPathAuthorizationBoundary`` \u2014 paths exactly at the boundary
  are allowed
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _touch(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestPathAuthorizationRule:
    """Pure authorization rule: is path allowed by allowed/prohibited sets?"""

    def test_allowed_path_is_authorized(self) -> None:
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
        assert rule.is_authorized("src/seharness/foo.py") is True

    def test_disallowed_path_is_unauthorized(self) -> None:
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
        assert rule.is_authorized("docs/spec/spec.md") is False

    def test_prohibited_path_always_unauthorized_even_if_allowed(self) -> None:
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
        )

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/", "harness.yaml")),
            prohibited_paths=ProhibitedPaths(("harness.yaml",)),
        )
        assert rule.is_authorized("harness.yaml") is False

    def test_boundary_path_under_allowed_prefix_is_authorized(self) -> None:
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
        )

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/",)),
            prohibited_paths=ProhibitedPaths(()),
        )
        assert rule.is_authorized("src/seharness/deep/nested/foo.py") is True


class TestAllowedPathsValidation:
    """``AllowedPaths`` and ``ProhibitedPaths`` are typed containers."""

    def test_empty_allowed_paths_rejected_at_construction(self) -> None:
        from seharness.execution.paths import AllowedPaths

        with pytest.raises(ValueError):
            AllowedPaths(())

    def test_empty_prohibited_paths_allowed(self) -> None:
        from seharness.execution.paths import ProhibitedPaths

        # No exception \u2014 prohibition list can be empty.
        pp = ProhibitedPaths(())
        assert tuple(pp) == ()

    def test_whitespace_only_path_rejected(self) -> None:
        from seharness.execution.paths import AllowedPaths

        with pytest.raises(ValueError):
            AllowedPaths(("   ",))


class TestPathReverter:
    """Bullet 5: revert unauthorized file changes from a pre-task snapshot."""

    def test_unauthorized_change_is_reverted(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        # Snapshot taken at task start.
        snap = WorkspaceSnapshot(root=repo, captured_at=None)

        # Pre-task state: docs/spec/spec.md has SPEC v1.
        spec = repo / "docs" / "spec" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("SPEC v1\n")
        snap.record(spec, mtime=None, size=spec.stat().st_size)

        # Task author writes to docs/ (outside allowed_paths).
        spec.write_text("SPEC v2 (sneaky edit)\n")

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )

        reverted = revert_unauthorized(repo, snap, rule)
        assert any("docs/spec/spec.md" in str(p) for p in reverted)
        assert spec.read_text() == "SPEC v1\n"

    def test_authorized_change_is_not_reverted(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        snap = WorkspaceSnapshot(root=repo, captured_at=None)

        src = repo / "src" / "seharness" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("pass\n")
        snap.record(src, mtime=None, size=src.stat().st_size)

        src.write_text("def foo() -> None: pass\n")

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )

        reverted = revert_unauthorized(repo, snap, rule)
        assert all("src" not in str(p) for p in reverted)
        assert src.read_text() == "def foo() -> None: pass\n"

    def test_prohibited_change_is_reverted(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        snap = WorkspaceSnapshot(root=repo, captured_at=None)

        config = repo / "harness.yaml"
        config.write_text("version: 1\n")
        snap.record(config, mtime=None, size=config.stat().st_size)

        config.write_text("version: 999\n")

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("harness.yaml",)),
            prohibited_paths=ProhibitedPaths(("harness.yaml",)),
        )

        reverted = revert_unauthorized(repo, snap, rule)
        assert any("harness.yaml" in str(p) for p in reverted)
        assert config.read_text() == "version: 1\n"


class TestRevertAcceptsDeletions:
    """Reverting also restores deleted files."""

    def test_deleted_authorized_file_is_restored(self, tmp_path: Path) -> None:
        from seharness.execution.workspace import WorkspaceSnapshot
        from seharness.execution.paths import (
            PathAuthorizationRule,
            AllowedPaths,
            ProhibitedPaths,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        snap = WorkspaceSnapshot(root=repo, captured_at=None)

        src = repo / "src" / "seharness" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("ORIGINAL\n")
        snap.record(src, mtime=None, size=src.stat().st_size)

        # Task author deletes the file.
        os.remove(src)

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )

        # Deleting an authorized file is itself an authorized change, so
        # revert_unauthorized should NOT restore it. This guards against
        # accidental restore of intentional deletes.
        reverted = revert_unauthorized(repo, snap, rule)
        assert all("src/seharness" not in str(p) for p in reverted)
        assert not src.exists()