"""Commit service + CommitMessage + AuthorizedFileSet.

Per SPEC §'19. Git Automation':
- Stage only authorized files.
- Commit format:
    feat(scope): description

    Feature: <id>
    Task: <id>
    Requirements: <ids>
    Scenarios: <ids>
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from seharness.delivery.backend import GitBackend


class UnauthorizedFileError(ValueError):
    """Raised when a file outside allowed_paths is staged."""


class AuthorizedFileSet(BaseModel):
    """Set of files allowed to be staged.

    Per slice 5/6/7: allowed_paths and prohibited_paths MUST NOT overlap.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _reject_overlap(self) -> AuthorizedFileSet:
        overlap = set(self.allowed_paths) & set(self.prohibited_paths)
        if overlap:
            raise ValueError(f"allowed_paths and prohibited_paths overlap: {sorted(overlap)}")
        return self


class CommitMessage(BaseModel):
    """Per SPEC §'19. Git Automation' commit format."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(min_length=1)
    description: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    requirement_ids: tuple[str, ...] = ()
    scenario_ids: tuple[str, ...] = ()

    def render(self) -> str:
        header = f"feat({self.scope}): {self.description}"
        requirements = ", ".join(self.requirement_ids)
        scenarios = ", ".join(self.scenario_ids)
        return (
            f"{header}\n\n"
            f"Feature: {self.feature_id}\n"
            f"Task: {self.task_id}\n"
            f"Requirements: {requirements}\n"
            f"Scenarios: {scenarios}\n"
        )


class CommitService:
    """Stages + commits only authorized files."""

    def __init__(self, *, backend: GitBackend) -> None:
        self._backend = backend

    def stage(
        self,
        *,
        repo_root: Path,
        files: tuple[str, ...],
        authorized: AuthorizedFileSet,
    ) -> None:
        # Validate ALL files first; reject entire call if any unauthorized.
        self._validate_authorized(files, authorized)
        self._backend.stage(repo_root, files)

    def commit(
        self,
        *,
        repo_root: Path,
        message: CommitMessage,
        author_name: str,
        author_email: str,
    ) -> str:
        return self._backend.commit(
            repo_root, message.render(), author_name=author_name, author_email=author_email
        )

    def _validate_authorized(self, files: tuple[str, ...], authorized: AuthorizedFileSet) -> None:
        allowed = set(authorized.allowed_paths)
        prohibited = set(authorized.prohibited_paths)
        for f in files:
            if ".." in f.split("/"):
                raise UnauthorizedFileError(f"path traversal rejected: {f!r}")
            if f in prohibited:
                raise UnauthorizedFileError(f"file {f!r} is in prohibited_paths")
            if f not in allowed:
                raise UnauthorizedFileError(f"file {f!r} is not in allowed_paths")
