"""Cluster M3-4: tests for LLMDrivenTaskRunner.

The runner is the M3-4 offline acceptance's bridge between the
model-produced ``attempted_changes`` and real pytest runs in
``tmp_path``. It enforces:

1. ``WRITE_FILE:`` directives parse cleanly with a header line
   and a content body.
2. Absolute paths and ``../`` escapes are rejected.
3. Paths outside the configured ``allowed_paths`` are rejected.
4. RED pytest runs BEFORE any patch is applied.
5. GREEN pytest runs AFTER the patch.
6. The final diff is captured via ``git diff`` against the
   initial commit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

# Pre-import to break the orchestrator's package init cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.orchestrator.llm_task_runner import (
    WRITE_FILE_HEADER,
    LLMDrivenTaskRunner,
    apply_controlled_patch_changes,
    apply_write_directives,
    parse_write_directives,
)

# ---------------------------------------------------------------------------
# parse_write_directives
# ---------------------------------------------------------------------------


class TestParseWriteDirectivesHappyPath:
    def test_single_write(self, tmp_path: Path) -> None:
        directives = parse_write_directives(
            [f"{WRITE_FILE_HEADER} main.py\nprint('hello')\n"],
            repo_root=tmp_path,
            allowed_paths=["main.py"],
        )
        assert len(directives) == 1
        assert directives[0].target_path == Path("main.py")
        assert directives[0].content == "print('hello')\n"

    def test_multiple_writes(self, tmp_path: Path) -> None:
        directives = parse_write_directives(
            [
                f"{WRITE_FILE_HEADER} a.py\nA\n",
                f"{WRITE_FILE_HEADER} subdir/b.py\nB\n",
            ],
            repo_root=tmp_path,
            allowed_paths=["a.py", "subdir/"],
        )
        assert len(directives) == 2
        assert [d.target_path for d in directives] == [
            Path("a.py"),
            Path("subdir/b.py"),
        ]

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        assert parse_write_directives([], repo_root=tmp_path, allowed_paths=["main.py"]) == ()

    def test_empty_entries_skipped(self, tmp_path: Path) -> None:
        # Empty strings in the list are skipped (the model may emit
        # an empty string when "no changes" is the answer).
        directives = parse_write_directives(
            ["", f"{WRITE_FILE_HEADER} main.py\nx\n", ""],
            repo_root=tmp_path,
            allowed_paths=["main.py"],
        )
        assert len(directives) == 1


class TestParseWriteDirectivesRefusals:
    def test_missing_newline_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="missing newline"):
            parse_write_directives(
                [f"{WRITE_FILE_HEADER} main.py"],  # no body
                repo_root=tmp_path,
                allowed_paths=["main.py"],
            )

    def test_wrong_header_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must start with"):
            parse_write_directives(
                ["DELETE_FILE: main.py\nx\n"],
                repo_root=tmp_path,
                allowed_paths=["main.py"],
            )

    def test_empty_target_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="empty target path"):
            parse_write_directives(
                [f"{WRITE_FILE_HEADER}  \nbody\n"],
                repo_root=tmp_path,
                allowed_paths=["main.py"],
            )

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must be relative"):
            parse_write_directives(
                [f"{WRITE_FILE_HEADER} /etc/passwd\nx\n"],
                repo_root=tmp_path,
                allowed_paths=["/etc/"],
            )

    def test_parent_escape_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="escapes repo_root"):
            parse_write_directives(
                [f"{WRITE_FILE_HEADER} ../escape.py\nx\n"],
                repo_root=tmp_path,
                allowed_paths=["../"],
            )

    def test_outside_allowed_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside the sandbox"):
            parse_write_directives(
                [f"{WRITE_FILE_HEADER} secret.py\nx\n"],
                repo_root=tmp_path,
                allowed_paths=["main.py"],
            )


# ---------------------------------------------------------------------------
# apply_write_directives
# ---------------------------------------------------------------------------


class TestApplyWriteDirectives:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        directives = parse_write_directives(
            [f"{WRITE_FILE_HEADER} subdir/new.py\nhello\n"],
            repo_root=tmp_path,
            allowed_paths=["subdir/"],
        )
        written = apply_write_directives(directives, repo_root=tmp_path)
        assert written == (tmp_path / "subdir" / "new.py",)
        assert (tmp_path / "subdir" / "new.py").read_text() == "hello\n"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("old\n")
        directives = parse_write_directives(
            [f"{WRITE_FILE_HEADER} main.py\nnew\n"],
            repo_root=tmp_path,
            allowed_paths=["main.py"],
        )
        apply_write_directives(directives, repo_root=tmp_path)
        assert (tmp_path / "main.py").read_text() == "new\n"

    def test_returns_written_paths(self, tmp_path: Path) -> None:
        directives = parse_write_directives(
            [
                f"{WRITE_FILE_HEADER} a.py\nA\n",
                f"{WRITE_FILE_HEADER} b.py\nB\n",
            ],
            repo_root=tmp_path,
            allowed_paths=["a.py", "b.py"],
        )
        written = apply_write_directives(directives, repo_root=tmp_path)
        assert written == (tmp_path / "a.py", tmp_path / "b.py")


# ---------------------------------------------------------------------------
# LLMDrivenTaskRunner end-to-end
# ---------------------------------------------------------------------------


def _git_init(tmp_path: Path) -> None:
    """Initialize a git repo with one initial commit so the runner's
    ``git diff`` command has a base to compare against.
    """
    import shutil as _shutil

    git_path = _shutil.which("git")
    if git_path is None:
        pytest.skip("git executable not on PATH")
    env = {
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "PATH": "/usr/bin:/usr/local/bin:/bin",
    }
    for cmd in (
        [git_path, "init", "--initial-branch=main"],
        [git_path, "config", "user.email", "test@example.invalid"],
        [git_path, "config", "user.name", "test"],
        [git_path, "add", "."],
        [git_path, "commit", "-m", "init"],
    ):
        subprocess.run(  # nosec B603
            cmd, cwd=tmp_path, capture_output=True, check=True, env=env, text=True
        )


def _make_fastapi_fixture_repo(tmp_path: Path) -> Path:
    """Create a tiny FastAPI repo at ``tmp_path`` with no /health."""
    (tmp_path / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n"
        "@app.get('/')\ndef root() -> dict[str, str]:\n    return {'msg': 'fixture'}\n"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_health.py").write_text(
        "from fastapi.testclient import TestClient\n"
        "from main import app\n\n"
        "def test_health_returns_ok() -> None:\n"
        "    client = TestClient(app)\n"
        "    response = client.get('/health')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json() == {'status': 'ok'}\n"
    )
    _git_init(tmp_path)
    return tmp_path


def _patch_envelope(*, kind: str, target_path: str, diff_text: str) -> str:
    return json.dumps(
        {
            "diff_text": diff_text,
            "task_id": "task-001",
            "kind": kind,
            "target_paths": [target_path],
        }
    )


def _health_production_patch() -> str:
    return _patch_envelope(
        kind="production_patch",
        target_path="main.py",
        diff_text=(
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -5,3 +5,8 @@ app = FastAPI()\n"
            " @app.get('/')\n"
            " def root() -> dict[str, str]:\n"
            "     return {'msg': 'fixture'}\n"
            "+\n"
            "+\n"
            "+@app.get('/health')\n"
            "+def health() -> dict[str, str]:\n"
            "+    return {'status': 'ok'}\n"
        ),
    )


class TestLLMDrivenTaskRunnerEndToEnd:
    """End-to-end: RED fails, patch applied, GREEN passes, final
    diff captured. This is the offline vertical acceptance's
    core RED+GREEN cycle.
    """

    def test_red_fails_then_green_passes(self, tmp_path: Path) -> None:
        repo = _make_fastapi_fixture_repo(tmp_path)

        patch = _health_production_patch()

        runner = LLMDrivenTaskRunner(
            repo_root=repo,
            pytest_target="tests/test_health.py",
            allowed_paths=["main.py", "tests/"],
        )
        red_dir = tmp_path / "red"
        green_dir = tmp_path / "green"
        result = runner.run_task(
            red_dir=red_dir,
            green_dir=green_dir,
            task_id="task-001",
            pending_changes=[patch],
        )

        # RED failed because /health was missing.
        red_json = json.loads((red_dir / "result.json").read_text())
        assert red_json["exit_code"] != 0
        assert red_json["failure_kind"] == "expected_failure"

        # GREEN passed because /health is now defined.
        green_json = json.loads((green_dir / "result.json").read_text())
        if green_json["exit_code"] != 0:
            stderr_text = (
                (green_dir / "stderr.txt").read_text()
                if (green_dir / "stderr.txt").exists()
                else "<no stderr.txt>"
            )
            stdout_text = (
                (green_dir / "stdout.txt").read_text()
                if (green_dir / "stdout.txt").exists()
                else "<no stdout.txt>"
            )
            pytest.fail(
                f"GREEN exit_code={green_json['exit_code']} (expected 0)\n"
                f"--- pytest stdout ---\n{stdout_text}\n"
                f"--- pytest stderr ---\n{stderr_text}"
            )
        assert green_json["exit_code"] == 0

        # The runner's return value reflects GREEN.
        assert result.exit_code == 0

        # The final diff was captured.
        diff_path = red_dir.parent / "final-diff.patch"
        assert diff_path.exists()
        diff_text = diff_path.read_text()
        assert "main.py" in diff_text
        assert "/health" in diff_text

    def test_no_patch_runs_red_then_green_with_failing_test(self, tmp_path: Path) -> None:
        """When ``pending_changes`` is empty the runner still runs
        RED + GREEN, but GREEN fails because the test still fails.
        This pins the "test patch is required" invariant.
        """
        repo = _make_fastapi_fixture_repo(tmp_path)
        runner = LLMDrivenTaskRunner(
            repo_root=repo,
            pytest_target="tests/test_health.py",
            allowed_paths=["main.py", "tests/"],
        )
        red_dir = tmp_path / "red"
        green_dir = tmp_path / "green"
        result = runner.run_task(
            red_dir=red_dir,
            green_dir=green_dir,
            task_id="task-001",
            pending_changes=None,
        )
        # Both RED and GREEN fail because no patch was applied.
        red_json = json.loads((red_dir / "result.json").read_text())
        green_json = json.loads((green_dir / "result.json").read_text())
        assert red_json["exit_code"] != 0
        assert green_json["exit_code"] != 0
        assert result.exit_code != 0

    def test_disallowed_path_rejected(self, tmp_path: Path) -> None:
        """A directive targeting a path outside ``allowed_paths``
        raises at apply time, before pytest runs.
        """
        repo = _make_fastapi_fixture_repo(tmp_path)
        runner = LLMDrivenTaskRunner(
            repo_root=repo,
            pytest_target="tests/test_health.py",
            allowed_paths=["main.py"],  # tests/ is NOT allowed
        )
        red_dir = tmp_path / "red"
        green_dir = tmp_path / "green"
        bad_patch = _patch_envelope(
            kind="production_patch",
            target_path="tests/test_health.py",
            diff_text=(
                "diff --git a/tests/test_health.py b/tests/test_health.py\n"
                "--- a/tests/test_health.py\n"
                "+++ b/tests/test_health.py\n"
                "@@ -1,1 +1,2 @@\n"
                " from fastapi.testclient import TestClient\n"
                "+# hijack\n"
            ),
        )
        with pytest.raises(ValueError, match="outside policy"):
            runner.run_task(
                red_dir=red_dir,
                green_dir=green_dir,
                task_id="task-001",
                pending_changes=[bad_patch],
            )

    def test_capture_passed_tests_from_green_stdout(self, tmp_path: Path) -> None:
        """When GREEN passes, ``covered_tests`` carries the test ids
        pytest reported as PASSED. This populates the validator's
        ``required_tests`` field.
        """
        repo = _make_fastapi_fixture_repo(tmp_path)
        patch = _health_production_patch()
        runner = LLMDrivenTaskRunner(
            repo_root=repo,
            pytest_target="tests/test_health.py",
            allowed_paths=["main.py", "tests/"],
        )
        red_dir = tmp_path / "red"
        green_dir = tmp_path / "green"
        runner.run_task(
            red_dir=red_dir,
            green_dir=green_dir,
            task_id="task-001",
            pending_changes=[patch],
        )
        green_json = json.loads((green_dir / "result.json").read_text())
        if green_json["exit_code"] != 0:
            stderr_text = (
                (green_dir / "stderr.txt").read_text()
                if (green_dir / "stderr.txt").exists()
                else "<no stderr.txt>"
            )
            stdout_text = (
                (green_dir / "stdout.txt").read_text()
                if (green_dir / "stdout.txt").exists()
                else "<no stdout.txt>"
            )
            pytest.fail(
                f"GREEN exit_code={green_json['exit_code']} (expected 0)\n"
                f"--- pytest stdout ---\n{stdout_text}\n"
                f"--- pytest stderr ---\n{stderr_text}"
            )
        # covered_tests is a list of test ids; pytest reports at
        # least the one targeted test.
        covered = green_json["covered_tests"]
        assert isinstance(covered, list)
        assert any("test_health" in t for t in covered)


class TestControlledPatchRoleBoundaries:
    def test_test_patch_rejects_source_path(self, tmp_path: Path) -> None:
        patch = _patch_envelope(
            kind="test_patch",
            target_path="main.py",
            diff_text=(
                "diff --git a/main.py b/main.py\n"
                "--- /dev/null\n"
                "+++ b/main.py\n"
                "@@ -0,0 +1 @@\n"
                "+unsafe = True\n"
            ),
        )
        with pytest.raises(ValueError, match="test paths only"):
            apply_controlled_patch_changes(
                [patch],
                repo_root=tmp_path,
                allowed_paths=("main.py", "tests/"),
                expected_kind="test_patch",
            )

    def test_production_patch_rejects_test_path(self, tmp_path: Path) -> None:
        patch = _patch_envelope(
            kind="production_patch",
            target_path="tests/test_x.py",
            diff_text=(
                "diff --git a/tests/test_x.py b/tests/test_x.py\n"
                "--- /dev/null\n"
                "+++ b/tests/test_x.py\n"
                "@@ -0,0 +1 @@\n"
                "+def test_x(): pass\n"
            ),
        )
        with pytest.raises(ValueError, match="may not touch test paths"):
            apply_controlled_patch_changes(
                [patch],
                repo_root=tmp_path,
                allowed_paths=("main.py", "tests/"),
                expected_kind="production_patch",
            )

    def test_declared_target_must_match_diff(self, tmp_path: Path) -> None:
        patch = _patch_envelope(
            kind="production_patch",
            target_path="other.py",
            diff_text=(
                "diff --git a/main.py b/main.py\n"
                "--- /dev/null\n"
                "+++ b/main.py\n"
                "@@ -0,0 +1 @@\n"
                "+safe = True\n"
            ),
        )
        with pytest.raises(ValueError, match="outside declared target_paths"):
            apply_controlled_patch_changes(
                [patch],
                repo_root=tmp_path,
                allowed_paths=("main.py",),
                expected_kind="production_patch",
            )
