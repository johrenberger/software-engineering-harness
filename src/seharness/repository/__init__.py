"""Framework-neutral repository discovery for the software engineering harness.

Slice 3 deliverables (per SPEC.md §5 / §17):

* :class:`RepositoryProfile` — Pydantic v2 model describing the target
  repository's layout, tooling, and conventions. Frozen after build;
  ``extra='forbid'`` so any unknown attribute is a hard error.
* :func:`inspect_repository` — framework-neutral inspector that derives
  the profile from a path on disk.
* :class:`CommandResolver` — turns the profile into concrete shell
  command strings (test, lint, type-check, format). Prefers repository-
  native tools (uv, poetry, pdm, hatch) over arbitrary ones.
* :class:`BaselineRecorder` — reads ``<run-dir>/.baseline/`` JSON
  snapshots written by slice 7. Slice 3 itself does not run subprocesses;
  it only persists and re-reads the snapshots.

This module never imports :mod:`subprocess` and never calls :func:`os.system`.
Running validation commands belongs to slice 7 (``validation/runner.py``).
"""

from __future__ import annotations

from .conventions import BaselineRecorder, CommandResolver, Gate
from .discovery import (
    BaselineSnapshot,
    BaselineStatus,
    FrameworkIndicator,
    PackageManager,
    RepositoryError,
    RepositoryProfile,
    ValidationCommand,
    inspect_repository,
)

__all__ = [
    "BaselineRecorder",
    "BaselineSnapshot",
    "BaselineStatus",
    "CommandResolver",
    "FrameworkIndicator",
    "Gate",
    "PackageManager",
    "RepositoryError",
    "RepositoryProfile",
    "ValidationCommand",
    "inspect_repository",
]
