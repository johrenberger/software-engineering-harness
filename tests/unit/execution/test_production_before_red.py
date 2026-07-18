"""RED \u2014 Slice 6 bullet 3: production changes before RED are rejected.

Per SPEC \u00a7"TDD evidence": "production files changed before RED evidence
was captured" must reject task completion.

The mechanism: the controller must compare the current production
source tree against a snapshot taken *before* the task started. Any
production path that changed between task-start and "RED captured"
is a violation.

The validator distinguishes:
- ``production_path`` \u2014 a path under the production source tree
  (e.g. ``src/seharness/...``). Changing these BEFORE red/ evidence
  is captured is a violation.
- ``test_path`` \u2014 a path under ``tests/...`` or other test directories.
  These are the ONLY paths allowed to change before RED evidence.
- ``execution_artifact_path`` \u2014 a path under
  ``execution/<task-id>/...``. These are written as part of the
  RED/GREEN capture process and are exempt.

Layout:
- ``TestProductionChangeBeforeRed`` \u2014 src/ modification before red/
- ``TestAllowedPreRedChanges`` \u2014 test path / execution artifact
  changes are fine
- ``TestPathClassification`` \u2014 the production/test/artifact
  classifier is pure and testable in isolation
"""

from __future__ import annotations

import pytest


class TestPathClassification:
    """PathClassifier must put each path into the right bucket."""

    def test_src_path_is_production(self) -> None:
        from seharness.execution.workspace import PathClassifier

        cls = PathClassifier(repo_root="/repo")
        assert cls.classify("src/seharness/foo.py") == "production"
        assert cls.classify("/repo/src/seharness/foo.py") == "production"

    def test_tests_path_is_test(self) -> None:
        from seharness.execution.workspace import PathClassifier

        cls = PathClassifier(repo_root="/repo")
        assert cls.classify("tests/unit/foo.py") == "test"
        assert cls.classify("/repo/tests/unit/foo.py") == "test"

    def test_execution_artifact_path_is_artifact(self) -> None:
        from seharness.execution.workspace import PathClassifier

        cls = PathClassifier(repo_root="/repo", task_id="T-001")
        assert cls.classify("execution/06-tdd-task-execution/01-red-required/red/result.json") == (
            "execution_artifact"
        )
        assert cls.classify("/repo/execution/T-001/green/result.json") == "execution_artifact"

    def test_unknown_path_is_other(self) -> None:
        from seharness.execution.workspace import PathClassifier

        cls = PathClassifier(repo_root="/repo")
        assert cls.classify("README.md") == "other"
        assert cls.classify("docs/spec/spec.md") == "other"


class TestProductionChangeBeforeRed:
    """Bullet 3: production change before red/ evidence is captured."""

    def test_src_modified_before_red_is_violation(self, tmp_path) -> None:
        from datetime import datetime, timedelta, timezone

        from seharness.execution.workspace import (
            PathClassifier,
            WorkspaceSnapshot,
            detect_pre_red_violations,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        src_file = repo / "src" / "foo.py"
        src_file.write_text("pass\n")

        # Snapshot taken at task start.
        start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = WorkspaceSnapshot(root=repo, captured_at=start)
        snapshot.record(src_file, mtime=start, size=src_file.stat().st_size)

        # Production file changed 5 minutes later, before RED is captured.
        later = start + timedelta(minutes=5)
        src_file.write_text("def new() -> None: ...\n")
        import os
        os.utime(src_file, (later.timestamp(), later.timestamp()))

        # RED captured AFTER the production change.
        red_captured_at = later + timedelta(seconds=10)

        violations = detect_pre_red_violations(
            snapshot=snapshot,
            classifier=PathClassifier(repo_root=str(repo)),
            red_captured_at=red_captured_at,
        )

        assert any("src/foo.py" in v.path for v in violations)


class TestAllowedPreRedChanges:
    """Test paths and execution artifacts are NOT violations."""

    def test_test_path_change_is_not_a_violation(self, tmp_path) -> None:
        from datetime import datetime, timedelta, timezone

        from seharness.execution.workspace import (
            PathClassifier,
            WorkspaceSnapshot,
            detect_pre_red_violations,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        test_file = repo / "tests" / "unit" / "foo.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_x() -> None: assert False\n")

        start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = WorkspaceSnapshot(root=repo, captured_at=start)
        snapshot.record(test_file, mtime=start, size=test_file.stat().st_size)

        later = start + timedelta(minutes=5)
        test_file.write_text("def test_x() -> None: assert True\n")
        import os
        os.utime(test_file, (later.timestamp(), later.timestamp()))

        violations = detect_pre_red_violations(
            snapshot=snapshot,
            classifier=PathClassifier(repo_root=str(repo)),
            red_captured_at=later + timedelta(seconds=10),
        )

        assert violations == []

    def test_execution_artifact_change_is_not_a_violation(self, tmp_path) -> None:
        from datetime import datetime, timedelta, timezone

        from seharness.execution.workspace import (
            PathClassifier,
            WorkspaceSnapshot,
            detect_pre_red_violations,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        art_dir = repo / "execution" / "T-1"
        art_dir.mkdir(parents=True)
        red_dir = art_dir / "red"
        red_dir.mkdir()
        result = red_dir / "result.json"
        result.write_text('{"phase": "red", "exit_code": 1}\n')

        start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = WorkspaceSnapshot(root=repo, captured_at=start)
        snapshot.record(result, mtime=start, size=result.stat().st_size)

        later = start + timedelta(minutes=5)
        result.write_text('{"phase": "red", "exit_code": 1, "duration_s": 0.1}\n')
        import os
        os.utime(result, (later.timestamp(), later.timestamp()))

        violations = detect_pre_red_violations(
            snapshot=snapshot,
            classifier=PathClassifier(repo_root=str(repo), task_id="T-1"),
            red_captured_at=later + timedelta(seconds=10),
        )

        assert violations == []


class TestPreRedViolationShape:
    """The violation record carries enough info for the rejection message."""

    def test_violation_carries_path_and_classification(self, tmp_path) -> None:
        from datetime import datetime, timezone

        from seharness.execution.workspace import (
            PathClassifier,
            PreRedViolation,
            WorkspaceSnapshot,
            detect_pre_red_violations,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        src_file = repo / "src" / "foo.py"
        src_file.write_text("x = 1\n")

        start = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = WorkspaceSnapshot(root=repo, captured_at=start)
        snapshot.record(src_file, mtime=start, size=src_file.stat().st_size)

        violations = detect_pre_red_violations(
            snapshot=snapshot,
            classifier=PathClassifier(repo_root=str(repo)),
            red_captured_at=start,
        )

        # We assert the *shape* of the dataclass without making changes,
        # so violations should be empty \u2014 but the shape test below
        # constructs a violation directly to verify fields.
        v = PreRedViolation(
            path="src/foo.py",
            classification="production",
            reason="mtime advanced before RED capture",
        )
        assert v.path == "src/foo.py"
        assert v.classification == "production"
        assert "mtime" in v.reason or "red" in v.reason.lower()