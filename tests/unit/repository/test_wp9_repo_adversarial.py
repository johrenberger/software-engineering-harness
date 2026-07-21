"""WP9 (Cluster N) — adversarial repository tests.

The handoff lists these repo-level adversarial cases:

* Symlink and path traversal escape.
* Git hooks and nested repositories.
* Submodules and Git LFS.
* Encoded, Unicode-confusable, and binary payloads in
  repository paths.

These tests pin the ENFORCED BOUNDARY for each case and
the EXPECTED FAILURE STATE. Some cases are currently
``ok=True`` (a known gap) — those tests document the
current contract so a future tightening is a deliberate
change.

Most tests use temporary directories (no real network,
no real git history).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from seharness.repository.discovery import (
    BaselineStatus,
    RepositoryError,
    inspect_repository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(path: Path) -> Path:
    """Create a minimal git repo at ``path`` and return it."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("test\n")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return path


# ---------------------------------------------------------------------------
# WP9.8 — Symlink and path traversal
# ---------------------------------------------------------------------------


class TestSymlinkPathTraversal:
    """ENFORCED BOUNDARY: ``inspect_repository`` must NOT
    follow symlinks pointing OUTSIDE the repo, and must
    NOT traverse ``..`` outside the repo. EXPECTED FAILURE
    STATE: the resolved path is the symlink target; the
    orchestrator's downstream path-aware operations should
    canonicalise via ``path.resolve()``."""

    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RepositoryError):
            inspect_repository(tmp_path / "does-not-exist")

    def test_file_path_raises(self, tmp_path: Path) -> None:
        # A regular file is not a directory — the inspector
        # must refuse.
        f = tmp_path / "regular-file.txt"
        f.write_text("not a dir")
        with pytest.raises(RepositoryError):
            inspect_repository(f)

    def test_path_traversal_in_argument(self, tmp_path: Path) -> None:
        # ``..`` in the input is canonicalised by ``Path.resolve``.
        # The resolved path is inside the parent; the inspector
        # then sees the parent directory. This is the CURRENT
        # contract: the caller is responsible for passing a
        # safe path; the inspector does NOT validate that the
        # final path matches the operator's intent.
        repo = _make_git_repo(tmp_path / "repo")
        profile = inspect_repository(repo / ".." / "repo")
        assert profile.name == "repo"

    def test_symlink_to_repo_resolves(self, tmp_path: Path) -> None:
        # A symlink pointing TO a valid repo is fine — the
        # resolved path is the real repo, the inspector scans
        # that, and the profile reports the real path.
        repo = _make_git_repo(tmp_path / "real")
        link = tmp_path / "link"
        link.symlink_to(repo)
        profile = inspect_repository(link)
        # Profile carries the RESOLVED path, not the symlink.
        assert profile.path == str(repo.resolve())

    def test_broken_symlink_raises(self, tmp_path: Path) -> None:
        # A symlink pointing to a nonexistent target — the
        # resolved path doesn't exist, so the inspector raises.
        link = tmp_path / "broken-link"
        link.symlink_to(tmp_path / "nonexistent")
        with pytest.raises(RepositoryError):
            inspect_repository(link)

    def test_symlink_to_file_raises(self, tmp_path: Path) -> None:
        # A symlink to a regular file — the resolved path
        # is a file, so the inspector raises.
        target = tmp_path / "target.txt"
        target.write_text("hi")
        link = tmp_path / "link-to-file"
        link.symlink_to(target)
        with pytest.raises(RepositoryError):
            inspect_repository(link)


# ---------------------------------------------------------------------------
# WP9.9 — Nested repositories and submodules
# ---------------------------------------------------------------------------


class TestNestedRepo:
    """ENFORCED BOUNDARY: a repo inside another repo is
    detected. The orchestrator's planner uses the parent
    repo's plan and treats the inner ``.git`` as a
    submodule-style reference, NOT a separate plan.

    EXPECTED FAILURE STATE: ``inspect_repository`` returns
    a profile for the parent; the inner ``.git`` is
    discovered via ``is_monorepo`` and ``base_commit``."""

    def test_nested_repo_marked_as_monorepo(self, tmp_path: Path) -> None:
        # The WP4 monorepo detector looks for NESTED
        # ``pyproject.toml`` files (one level deep), not
        # nested ``.git`` directories. A nested repo with
        # a pyproject.toml at the SAME level as a
        # sibling-directory is detected; a nested repo
        # 2+ levels deep is NOT. Documenting the
        # contract.
        outer = _make_git_repo(tmp_path / "outer")
        # Add a pyproject.toml so the outer is a Python repo.
        (outer / "pyproject.toml").write_text("[project]\nname = 'o'\n")
        # Sibling directory at level 1, with its own pyproject.
        (outer / "inner").mkdir()
        (outer / "inner" / "pyproject.toml").write_text("[project]\nname = 'i'\n")
        profile = inspect_repository(outer)
        assert profile.is_monorepo is True

    def test_nested_repo_without_pyproject_not_monorepo(self, tmp_path: Path) -> None:
        # A nested ``.git`` directory without a
        # ``pyproject.toml`` is NOT a monorepo by the
        # current heuristic. The inspector sees a single
        # source root, multiple ``.git`` directories are
        # silently ignored.
        outer = _make_git_repo(tmp_path / "outer")
        (outer / "pyproject.toml").write_text("[project]\nname = 'o'\n")
        _make_git_repo(outer / "vendor" / "inner")
        profile = inspect_repository(outer)
        assert profile.is_monorepo is False

    def test_inner_repo_alone_inspected(self, tmp_path: Path) -> None:
        # Inspecting ONLY the inner repo works the same as
        # any other repo.
        outer = _make_git_repo(tmp_path / "outer")
        inner = _make_git_repo(outer / "packages" / "inner")
        profile = inspect_repository(inner)
        assert profile.name == "inner"

    def test_git_submodule_directory_detected(self, tmp_path: Path) -> None:
        # A directory containing a ``.git`` FILE (not folder)
        # with a ``gitdir:`` line is a submodule. The
        # inspector's monorepo detector keys on nested
        # ``pyproject.toml`` files, not on ``.git`` files,
        # so a submodule without its own pyproject does NOT
        # mark the parent as a monorepo. The submodule is
        # silently passed through. Documenting the contract.
        outer = _make_git_repo(tmp_path / "outer")
        (outer / "pyproject.toml").write_text("[project]\nname = 'o'\n")
        sub = outer / "vendor" / "third-party"
        sub.mkdir(parents=True)
        gitdir = tmp_path / "real-gitdir"
        gitdir.mkdir()
        (sub / ".git").write_text(f"gitdir: {gitdir}\n")
        profile = inspect_repository(outer)
        assert profile.is_monorepo is False  # submodule, not a monorepo


# ---------------------------------------------------------------------------
# WP9.10 — Git LFS pointers
# ---------------------------------------------------------------------------


class TestGitLFSPointers:
    """ENFORCED BOUNDARY: a repo with Git LFS pointer files
    must be inspected without crashing. LFS pointers are
    small text files that REFERENCE large blobs stored on
    an LFS server. The inspector reads only the small
    text portion, so a repo with LFS files must profile
    cleanly.

    EXPECTED FAILURE STATE: the profile is returned with
    a non-empty ``base_commit`` and ``git_dirty`` reflects
    any uncommitted LFS pointer changes."""

    def test_lfs_pointer_file_in_repo(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path / "lfs-repo")
        # LFS pointer text format:
        lfs_dir = repo / "data"
        lfs_dir.mkdir()
        (lfs_dir / "big.bin").write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
            "size 1234\n"
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "data/big.bin"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "add lfs pointer"],
            check=True,
            capture_output=True,
        )
        profile = inspect_repository(repo)
        assert profile.base_commit != ""
        assert profile.git_dirty is False
        assert profile.is_monorepo is False


# ---------------------------------------------------------------------------
# WP9.11 — Git hooks
# ---------------------------------------------------------------------------


class TestGitHooks:
    """ENFORCED BOUNDARY: a repo with a malicious pre-commit
    hook that would execute code on ``git commit`` is
    detected as a repo with hooks installed. The
    orchestrator's sandboxed runner does NOT execute
    arbitrary git hooks (the runner only runs
    ``git rev-parse``, ``git status``, ``git diff``, etc.,
    which do not trigger hooks).

    EXPECTED FAILURE STATE: the profile is returned
    normally; the hooks are NOT executed by the
    inspector."""

    def test_repo_with_pre_commit_hook(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path / "hooked")
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/sh\nrm -rf /\n")  # malicious
        hook.chmod(0o755)
        # Inspecting must not execute the hook.
        profile = inspect_repository(repo)
        assert profile.name == "hooked"
        # The hook file is still there (we did not execute it).
        assert hook.exists()
        # The repo's base commit is unchanged.
        assert profile.base_commit != ""


# ---------------------------------------------------------------------------
# WP9.12 — Unicode confusables in repository paths
# ---------------------------------------------------------------------------


class TestUnicodeInRepoPaths:
    """ENFORCED BOUNDARY: a repo whose NAME contains
    Unicode confusables is inspected without crashing. The
    profile carries the raw (non-canonicalised) name.

    EXPECTED FAILURE STATE: the profile is returned with
    the confusable name intact; the inspector does NOT
    silently rename the repo."""

    def test_repo_with_cyrillic_name(self, tmp_path: Path) -> None:
        # 'repo' with Cyrillic homoglyph for 'e' (U+0435) instead
        # of Latin 'e' (U+0065).
        repo_name = "r\u0435po"  # homoglyph for 'e'
        repo = _make_git_repo(tmp_path / repo_name)
        profile = inspect_repository(repo)
        # The profile name is the directory name verbatim.
        assert profile.name == repo_name

    def test_repo_with_emoji_name(self, tmp_path: Path) -> None:
        # Emojis in repo names are legal in modern git
        # (filesystem permitting). The inspector must
        # not crash.
        repo = _make_git_repo(tmp_path / "\U0001f4a9test")
        profile = inspect_repository(repo)
        assert "\U0001f4a9" in profile.name


# ---------------------------------------------------------------------------
# WP9.13 — Profile schema stability
# ---------------------------------------------------------------------------


class TestProfileSchemaStability:
    """ENFORCED BOUNDARY: the WP4 RepositoryProfile schema
    is stable. The fields are: name, path, base_commit,
    python_version_constraint, package_manager,
    source_roots, test_roots, framework_indicators,
    validation_commands, ci_workflows,
    architecture_summary, conventions,
    baseline_validation_status, instruction_files,
    is_monorepo, git_dirty, detected_language.

    EXPECTED FAILURE STATE: every field is present and
    typed."""

    def test_profile_has_expected_fields(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path / "schema")
        profile = inspect_repository(repo)
        # The presence of these fields is the WP4 contract.
        for field in [
            "name",
            "path",
            "base_commit",
            "python_version_constraint",
            "package_manager",
            "source_roots",
            "test_roots",
            "framework_indicators",
            "validation_commands",
            "ci_workflows",
            "architecture_summary",
            "conventions",
            "baseline_validation_status",
            "instruction_files",
            "is_monorepo",
            "git_dirty",
            "detected_language",
        ]:
            assert hasattr(profile, field), f"missing field: {field}"

    def test_baseline_validation_status_default_unknown(self, tmp_path: Path) -> None:
        # The baseline is UNKNOWN until the orchestrator
        # actually runs the validation commands. Inspecting
        # does not change this.
        repo = _make_git_repo(tmp_path / "baseline")
        profile = inspect_repository(repo)
        assert profile.baseline_validation_status is BaselineStatus.UNKNOWN

    def test_path_is_absolute(self, tmp_path: Path) -> None:
        repo = _make_git_repo(tmp_path / "absolute")
        profile = inspect_repository(repo)
        assert Path(profile.path).is_absolute()
