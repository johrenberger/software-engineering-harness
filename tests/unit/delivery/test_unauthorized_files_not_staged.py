"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 2.

'Unauthorized files are not staged':
- The commit service MUST reject commits that include files outside the
  approved allowed_paths list.
- Files explicitly in prohibited_paths MUST never be staged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.delivery.commit import (
    AuthorizedFileSet,
    CommitService,
    UnauthorizedFileError,
)
from seharness.delivery.backend import (
    GitBackend,
    SubprocessGitBackend,
)


class _FakeBackend(GitBackend):
    """Records stage calls; doesn't actually run git."""

    def __init__(self) -> None:
        self.staged: list[str] = []

    def stage(self, repo_root: Path, files: tuple[str, ...]) -> None:
        self.staged.extend(files)

    def commit(
        self,
        repo_root: Path,
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str:
        return "deadbeef"

    def current_sha(self, repo_root: Path) -> str:
        return "deadbeef"


def _file_set(
    allowed: tuple[str, ...] = (),
    prohibited: tuple[str, ...] = (),
) -> AuthorizedFileSet:
    return AuthorizedFileSet(allowed_paths=allowed, prohibited_paths=prohibited)


def test_unauthorized_file_rejected_at_stage() -> None:
    """Files outside allowed_paths MUST be rejected."""
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(allowed=("src/seharness/x.py",))
    with pytest.raises(UnauthorizedFileError, match="src/seharness/y.py"):
        service.stage(
            repo_root=Path("/tmp"),
            files=("src/seharness/y.py",),
            authorized=files,
        )


def test_prohibited_file_rejected_even_if_allowed() -> None:
    """prohibited_paths wins over allowed_paths (deny list)."""
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(
        allowed=("src/seharness/x.py",),
        prohibited=("src/seharness/x.py",),
    )
    with pytest.raises(UnauthorizedFileError, match="prohibited"):
        service.stage(
            repo_root=Path("/tmp"),
            files=("src/seharness/x.py",),
            authorized=files,
        )


def test_authorized_file_staged() -> None:
    """A file in allowed_paths (not in prohibited) MUST be staged."""
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(allowed=("src/seharness/x.py",))
    service.stage(
        repo_root=Path("/tmp"),
        files=("src/seharness/x.py",),
        authorized=files,
    )
    assert "src/seharness/x.py" in backend.staged


def test_mixed_authorized_and_unauthorized_rejects_entire_call() -> None:
    """If any file is unauthorized, the ENTIRE stage call fails (no partial)."""
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(allowed=("src/seharness/x.py",))
    with pytest.raises(UnauthorizedFileError):
        service.stage(
            repo_root=Path("/tmp"),
            files=("src/seharness/x.py", "src/seharness/y.py"),
            authorized=files,
        )
    # No files staged because validation is atomic.
    assert backend.staged == []


def test_empty_authorized_set_rejects_everything() -> None:
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(allowed=())
    with pytest.raises(UnauthorizedFileError):
        service.stage(
            repo_root=Path("/tmp"),
            files=("src/seharness/x.py",),
            authorized=files,
        )


def test_authorized_file_set_rejects_overlap() -> None:
    """allowed_paths and prohibited_paths MUST NOT overlap (per slice 5/6/7)."""
    with pytest.raises(Exception):  # noqa: B017
        AuthorizedFileSet(
            allowed_paths=("src/a.py",),
            prohibited_paths=("src/a.py",),
        )


def test_subprocess_git_backend_exists() -> None:
    """Production backend: SubprocessGitBackend. Constructible without args."""
    backend = SubprocessGitBackend()
    assert hasattr(backend, "stage")
    assert hasattr(backend, "commit")
    assert hasattr(backend, "current_sha")


def test_path_traversal_rejected() -> None:
    """Path traversal ('../') MUST be rejected at stage time."""
    backend = _FakeBackend()
    service = CommitService(backend=backend)
    files = _file_set(allowed=("../etc/passwd",))
    with pytest.raises(UnauthorizedFileError, match="traversal"):
        service.stage(
            repo_root=Path("/tmp"),
            files=("../etc/passwd",),
            authorized=files,
        )