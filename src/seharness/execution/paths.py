"""Path authorization primitives for slice 6.

Per SPEC \u00a7"Implementation task" and slice 6 RED bullet 5, every task
declares:

- ``allowed_paths`` \u2014 the directories/files the task may modify.
  Empty is a construction error (the validator will refuse anyway).
- ``prohibited_paths`` \u2014 explicit denials. If a path appears in
  BOTH ``allowed_paths`` and ``prohibited_paths``, construction fails
  (overlap is undefined behaviour).

``PathAuthorizationRule.is_authorized(path)`` is the single source of
truth. Paths are normalized via ``os.path.normpath`` so
``./src/seharness/foo.py`` and ``src/seharness/foo.py`` resolve
identically.

The reverter (``revert_unauthorized``) lives in ``workspace.py``
because it depends on ``WorkspaceSnapshot``; this module stays pure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AllowedPaths:
    """A non-empty tuple of path prefixes the task is allowed to modify.

    Empty construction is rejected; slice 5 ``PlanValidator`` rejects
    empty ``allowed_paths`` at the plan level too, so this is defence
    in depth.
    """

    entries: tuple[str, ...]

    def __post_init__(self) -> None:
        cleaned = tuple(e.strip() for e in self.entries)
        if not cleaned:
            raise ValueError("AllowedPaths must contain at least one entry")
        if any(not e for e in cleaned):
            raise ValueError("AllowedPaths entries must be non-empty after strip")
        # Freeze the cleaned values back.
        object.__setattr__(self, "entries", cleaned)


@dataclass(frozen=True)
class ProhibitedPaths:
    """A tuple of path prefixes the task must NOT modify."""

    entries: tuple[str, ...]

    def __post_init__(self) -> None:
        cleaned = tuple(e.strip() for e in self.entries)
        # Empty prohibition list is allowed.
        object.__setattr__(self, "entries", cleaned)


@dataclass(frozen=True)
class PathAuthorizationRule:
    """Pure rule: is ``path`` authorized given allowed/prohibited sets?

    Construction rejects overlap between ``allowed_paths`` and
    ``prohibited_paths`` (otherwise the answer depends on which check
    runs first, which is the kind of subtle bug slice 6 is meant to
    prevent).
    """

    task_id: str
    allowed_paths: AllowedPaths
    prohibited_paths: ProhibitedPaths

    def __post_init__(self) -> None:
        allowed_set = set(self.allowed_paths.entries)
        prohibited_set = set(self.prohibited_paths.entries)
        overlap = allowed_set & prohibited_set
        if overlap:
            raise ValueError(
                f"path(s) {sorted(overlap)} appear in both allowed_paths "
                f"and prohibited_paths for task {self.task_id!r}"
            )

    def is_authorized(self, path: str) -> bool:
        """Return True iff ``path`` is authorized.

        Algorithm:
        1. Normalize the path (``./`` and ``//`` collapsed).
        2. If any prohibited prefix matches, return False.
        3. If any allowed prefix matches, return True.
        4. Otherwise return False (default deny).
        """
        norm = self._normalize(path)
        for prohibited in self.prohibited_paths.entries:
            stripped = prohibited.rstrip("/")
            if norm == stripped or norm.startswith(prohibited):
                return False
        for allowed in self.allowed_paths.entries:
            prefix = allowed.rstrip("/")
            if norm == prefix or norm.startswith(prefix + "/") or norm.startswith(allowed):
                return True
        return False

    @staticmethod
    def _normalize(path: str) -> str:
        """Normalize a path string so caller variants compare equal."""
        return os.path.normpath(path)


__all__ = [
    "AllowedPaths",
    "PathAuthorizationRule",
    "ProhibitedPaths",
]
