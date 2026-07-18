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
* :class:`ConventionDetector` protocol + :class:`ConventionRegistry`
  for the plugin-friendly extension point asked for in the REFACTOR
  bullet.
* :class:`BaselineRecorder` — reads ``<run-dir>/.baseline/`` JSON
  snapshots written by slice 7. Slice 3 itself does not run subprocesses;
  it only persists and re-reads the snapshots.

This module never imports :mod:`subprocess` and never calls :func:`os.system`.
Running validation commands belongs to slice 7 (``validation/runner.py``).
"""

from __future__ import annotations

__all__: list[str] = []