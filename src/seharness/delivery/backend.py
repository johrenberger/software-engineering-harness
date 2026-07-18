"""Git backend Protocol + SubprocessGitBackend default implementation.

Per SPEC §'19. Git Automation': The controller owns Git.
"""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class GitBackend(Protocol):
    """Git operations interface. Implementations: SubprocessGitBackend, GitPythonBackend."""

    def create_branch(self, repo_root: Path, branch_name: str) -> None: ...

    def stage(self, repo_root: Path, files: tuple[str, ...]) -> None: ...

    def commit(
        self,
        repo_root: Path,
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str: ...

    def current_sha(self, repo_root: Path) -> str: ...


class SubprocessGitBackend:
    """Default Git backend using subprocess."""

    def __init__(self, git_bin: str = "git") -> None:
        self._git = git_bin

    def _run(self, repo_root: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B602,B603
            (self._git, *args),
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

    def create_branch(self, repo_root: Path, branch_name: str) -> None:
        self._run(repo_root, ("checkout", "-b", branch_name))

    def stage(self, repo_root: Path, files: tuple[str, ...]) -> None:
        for f in files:
            self._run(repo_root, ("add", "--", f))

    def commit(
        self,
        repo_root: Path,
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str:
        subprocess.run(  # nosec B602,B603
            (
                self._git,
                "-c",
                f"user.name={author_name}",
                "-c",
                f"user.email={author_email}",
                "commit",
                "-m",
                message,
            ),
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            env=None,
        )
        sha = self.current_sha(repo_root)
        return sha

    def current_sha(self, repo_root: Path) -> str:
        result = self._run(repo_root, ("rev-parse", "HEAD"))
        return result.stdout.strip()
