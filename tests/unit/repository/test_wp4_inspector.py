"""Tests for WP4 / PR3 — extended inspect_repository output.

The handoff doc (WP4 acceptance criteria) requires the profile to
expose:

* instruction files (AGENTS.md, CONTRIBUTING.md, CODEOWNERS)
* monorepo flag
* base commit + dirty-state
* dominant-language detection

These tests pin those behaviors. They run against small fixture
directories built with ``tmp_path`` so the inspector never touches
the real repo on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from seharness.repository.discovery import inspect_repository


def _git(cwd: Path, *args: str) -> None:
    """Helper: run ``git`` in ``cwd`` with the standard config
    needed for commits in tests (no committer/author check, no
    signing). Fails the test if git exits non-zero.
    """
    subprocess.run(
        ("git", "-C", str(cwd), *args),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/usr/local/bin:/bin",
        },
    )


def _init_repo(path: Path) -> None:
    """Init a git repo with one empty commit so ``base_commit`` is
    populated.
    """
    _git(path, "init", "--quiet", "--initial-branch=main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    _git(path, "commit", "--allow-empty", "--quiet", "-m", "init")


class TestDetectInstructionFiles:
    """Cluster WP4 / story WP4.2: surface instruction files."""

    def test_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        profile = inspect_repository(tmp_path)
        assert "AGENTS.md" in profile.instruction_files

    def test_contributing_md(self, tmp_path: Path) -> None:
        (tmp_path / "CONTRIBUTING.md").write_text("# Contributing\n")
        profile = inspect_repository(tmp_path)
        assert "CONTRIBUTING.md" in profile.instruction_files

    def test_codeowners(self, tmp_path: Path) -> None:
        (tmp_path / "CODEOWNERS").write_text("* @owner\n")
        profile = inspect_repository(tmp_path)
        assert "CODEOWNERS" in profile.instruction_files

    def test_readme_md(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Demo\n")
        profile = inspect_repository(tmp_path)
        assert "README.md" in profile.instruction_files

    def test_no_instruction_files(self, tmp_path: Path) -> None:
        profile = inspect_repository(tmp_path)
        assert profile.instruction_files == ()

    def test_only_whitelisted_files_are_surfaced(self, tmp_path: Path) -> None:
        """Arbitrary dotfiles must not leak into instruction_files."""
        (tmp_path / "AGENTS.md").write_text("real\n")
        (tmp_path / "NOTES.txt").write_text("ignore\n")
        (tmp_path / "TODO.md").write_text("ignore\n")
        profile = inspect_repository(tmp_path)
        assert "NOTES.txt" not in profile.instruction_files
        assert "TODO.md" not in profile.instruction_files
        assert "AGENTS.md" in profile.instruction_files


class TestDetectMonorepo:
    """Cluster WP4 / story WP4.4: detect nested projects."""

    def test_single_project_is_not_monorepo(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        profile = inspect_repository(tmp_path)
        assert profile.is_monorepo is False

    def test_nested_pyproject_is_monorepo(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='root'\n")
        # The detector walks one level deep — put the nested
        # pyproject at ``<root>/<child>/pyproject.toml`` so the
        # detector sees it.
        child = tmp_path / "core"
        child.mkdir()
        (child / "pyproject.toml").write_text("[project]\nname='core'\n")
        profile = inspect_repository(tmp_path)
        assert profile.is_monorepo is True

    def test_repo_without_pyproject_is_not_monorepo(self, tmp_path: Path) -> None:
        # No pyproject at root, no nested projects — just files.
        (tmp_path / "main.py").write_text("print('hi')\n")
        profile = inspect_repository(tmp_path)
        assert profile.is_monorepo is False


class TestDetectGitState:
    """Cluster WP4 / story WP4.6: capture base commit + dirty flag."""

    def test_non_git_repo_has_empty_commit(self, tmp_path: Path) -> None:
        profile = inspect_repository(tmp_path)
        assert profile.base_commit == ""
        assert profile.git_dirty is False

    def test_clean_git_repo_has_commit(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "README.md").write_text("# Demo\n")
        _git(tmp_path, "add", "README.md")
        _git(tmp_path, "commit", "-m", "add readme")
        profile = inspect_repository(tmp_path)
        assert profile.base_commit  # 40-char hex SHA
        assert len(profile.base_commit) == 40
        assert profile.git_dirty is False

    def test_dirty_git_repo_flagged(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "uncommitted.txt").write_text("dirty\n")
        profile = inspect_repository(tmp_path)
        assert profile.base_commit  # HEAD still recorded
        assert profile.git_dirty is True


class TestDetectLanguage:
    """Cluster WP4 / story WP4.7: surface dominant language."""

    def test_python_repo(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "utils.py").write_text("y = 2\n")
        (tmp_path / "README.md").write_text("# Demo\n")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "python"

    def test_typescript_repo(self, tmp_path: Path) -> None:
        (tmp_path / "index.ts").write_text("const x = 1;\n")
        (tmp_path / "util.ts").write_text("export {};\n")
        (tmp_path / "package.json").write_text("{}\n")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "typescript"

    def test_javascript_repo(self, tmp_path: Path) -> None:
        (tmp_path / "index.js").write_text("const x = 1;\n")
        (tmp_path / "util.js").write_text("module.exports = {};\n")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "javascript"

    def test_rust_repo(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        (tmp_path / "main.rs").write_text("fn main() {}\n")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "rust"

    def test_go_repo(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        (tmp_path / "main.go").write_text("package main\n")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "go"

    def test_empty_repo_is_unknown(self, tmp_path: Path) -> None:
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "unknown"

    def test_python_dominates_when_mixed(self, tmp_path: Path) -> None:
        """When multiple languages exist, the one with more files wins."""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        (tmp_path / "c.py").write_text("")
        (tmp_path / "x.ts").write_text("")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "python"

    def test_scans_inside_src_subdir(self, tmp_path: Path) -> None:
        """Cluster WP4 / story WP4.7: the detector must walk into
        ``src/`` so src-layout repos are recognised.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "python"

    def test_ignores_unknown_subdirs(self, tmp_path: Path) -> None:
        """A random subdir like ``misc/`` should not be scanned —
        only the well-known package roots count.
        """
        (tmp_path / "misc").mkdir()
        (tmp_path / "misc" / "main.py").write_text("")
        profile = inspect_repository(tmp_path)
        # The .py file inside misc/ is NOT counted, so language is
        # unknown (top-level has nothing).
        assert profile.detected_language == "unknown"


class TestDeriveAllowedPaths:
    """Cluster WP4 / story WP4.5: derive allowed_paths from profile."""

    def test_uses_source_roots(self) -> None:
        from seharness.repository.discovery import (
            PackageManager,
            RepositoryProfile,
            derive_allowed_paths,
        )

        profile = RepositoryProfile(
            name="x",
            path="/tmp/x",
            base_commit="",
            python_version_constraint="",
            package_manager=PackageManager.UV,
            source_roots=("src", "lib"),
            test_roots=("tests",),
            framework_indicators=(),
            validation_commands=(),
            ci_workflows=(),
            architecture_summary="",
            conventions=(),
            baseline_validation_status="unknown",
            instruction_files=(),
            is_monorepo=False,
            git_dirty=False,
            detected_language="python",
        )
        paths = derive_allowed_paths(profile)
        assert "src/" in paths
        assert "lib/" in paths
        assert "tests/" in paths

    def test_includes_docs_when_present(self, tmp_path: Path) -> None:
        from seharness.repository.discovery import derive_allowed_paths

        (tmp_path / "docs").mkdir()
        profile = inspect_repository(tmp_path)
        paths = derive_allowed_paths(profile)
        assert "docs/" in paths

    def test_dedupes_when_source_and_test_overlap(self) -> None:
        """A common layout has tests/ as a subdir of src/ — we should
        only see each path once in the result."""
        from seharness.repository.discovery import (
            PackageManager,
            RepositoryProfile,
            derive_allowed_paths,
        )

        profile = RepositoryProfile(
            name="x",
            path="/tmp/x",
            base_commit="",
            python_version_constraint="",
            package_manager=PackageManager.UV,
            source_roots=("src",),
            test_roots=("src/tests",),
            framework_indicators=(),
            validation_commands=(),
            ci_workflows=(),
            architecture_summary="",
            conventions=(),
            baseline_validation_status="unknown",
            instruction_files=(),
            is_monorepo=False,
            git_dirty=False,
            detected_language="python",
        )
        paths = derive_allowed_paths(profile)
        # Both appear once; no duplicates.
        assert len(paths) == len(set(paths))


class TestInspectRepositoryIntegration:
    """End-to-end: a real-looking repo yields all WP4 fields populated."""

    def test_full_python_repo(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname="x"\nrequires-python=">=3.12"\n\n'
            '[tool.pytest]\nini_options = ["testpaths"]\n'
            "[tool.ruff]\nline-length = 100\n"
        )
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_x(): pass\n")
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "workflows").mkdir()
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
        # Commit everything so the repo is clean before we profile.
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-m", "fixture")
        profile = inspect_repository(tmp_path)
        assert profile.detected_language == "python"
        assert profile.package_manager in {PackageManager.UV, PackageManager.UNKNOWN}
        assert "AGENTS.md" in profile.instruction_files
        assert "src" in profile.source_roots
        assert "tests" in profile.test_roots
        assert profile.is_monorepo is False
        assert profile.architecture_summary == "src-layout Python package"
        assert profile.base_commit  # 40-char SHA
        assert profile.git_dirty is False
        # Conventions should pick up tool.ruff + tool.pytest
        assert any(c.startswith("tool.") for c in profile.conventions)
        # CI workflows
        assert any(wf.endswith("ci.yml") for wf in profile.ci_workflows)


# Import for the type annotation in TestDeriveAllowedPaths:
from seharness.repository.discovery import PackageManager  # noqa: E402
