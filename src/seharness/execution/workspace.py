"""Workspace mutation tracking for slice 6.

Per SPEC \u00a7"TDD evidence" and slice 6 RED bullets 3 + 5, the controller
must:

- Track the production source tree at task start (``WorkspaceSnapshot``).
- Classify paths as ``production`` / ``test`` / ``execution_artifact`` /
  ``other`` so the validator can flag pre-RED production changes
  (``detect_pre_red_violations``).
- Revert unauthorized changes after a task run (``revert_unauthorized``)
  using the same rule as ``PathAuthorizationRule``.

The ``PathClassifier`` uses the repo root and (optionally) the task id
to decide which bucket a path belongs to:

- ``src/<repo-package>/...`` \u2192 ``production``
- ``tests/...`` \u2192 ``test``
- ``execution/<task-id>/...`` \u2192 ``execution_artifact``
- everything else \u2192 ``other``

This module deliberately owns the reverter (not ``paths.py``) so the
snapshot reading and rule application are co-located.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from seharness.execution.paths import PathAuthorizationRule

# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

_PRODUCTION_PREFIXES = ("src/",)
_TEST_PREFIXES = ("tests/", "test/")


@dataclass(frozen=True)
class PreRedViolation:
    """One production-path change detected before RED was captured."""

    path: str
    classification: str  # "production" / "other"
    reason: str


@dataclass(frozen=True)
class PathClassifier:
    """Classify a relative path into ``production`` / ``test`` /
    ``execution_artifact`` / ``other``.

    ``repo_root`` is the directory whose ``src/`` and ``tests/`` trees
    are considered production/test. ``task_id`` is required to classify
    the execution-artifact bucket (paths under
    ``execution/<task_id>/...``).
    """

    repo_root: str
    task_id: str | None = None

    def classify(self, path: str) -> str:
        """Return one of: ``production`` / ``test`` /
        ``execution_artifact`` / ``other``."""
        norm = self._normalize(path)
        if self.task_id and norm.startswith(f"execution/{self.task_id}/"):
            return "execution_artifact"
        for prefix in _PRODUCTION_PREFIXES:
            if norm.startswith(prefix):
                return "production"
        for prefix in _TEST_PREFIXES:
            if norm.startswith(prefix):
                return "test"
        return "other"

    def _normalize(self, path: str) -> str:
        """Normalize: strip absolute ``repo_root`` prefix and leading
        ``./`` and convert backslashes."""
        s = path.replace("\\", "/")
        repo = self.repo_root.replace("\\", "/").rstrip("/")
        if s.startswith(repo + "/"):
            s = s[len(repo) + 1 :]
        s = s.lstrip("./")
        return s


# ---------------------------------------------------------------------------
# Workspace snapshot
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceSnapshot:
    """In-memory record of file content at a point in time.

    Captured at task start. ``record()`` stores both the relative path
    and the file content (so the reverter can restore content after a
    task run).

    This is a mutable-by-design dataclass: callers accumulate records
    via ``record()``. Once a snapshot is "frozen" by passing it to
    ``detect_pre_red_violations`` or ``revert_unauthorized``, no
    further mutation should occur.
    """

    root: Path
    captured_at: datetime | None
    _records: dict[str, _SnapshotRecord] = field(default_factory=dict, repr=False)

    def record(
        self,
        path: Path,
        *,
        mtime: datetime | None,
        size: int,
    ) -> None:
        """Capture ``path`` under the snapshot.

        The path is stored RELATIVE TO ``self.root`` so the snapshot
        is portable across machines. The file content is read
        eagerly \u2014 the reverter needs it.
        """
        rel = path.resolve().relative_to(self.root.resolve())
        rel_posix = rel.as_posix()
        content = path.read_bytes()
        self._records[rel_posix] = _SnapshotRecord(
            path=rel_posix, mtime=mtime, size=size, content=content
        )

    @property
    def paths(self) -> Iterator[str]:
        return iter(tuple(self._records))

    def content(self, rel_path: str) -> str | None:
        """Return the captured content for ``rel_path`` decoded as UTF-8.

        ``None`` is returned when the path is not in the snapshot, or
        when the captured content is not valid UTF-8 (binary files).
        Callers that need bytes should use ``content_bytes`` instead.
        """
        rec = self._records.get(rel_path)
        if rec is None:
            return None
        try:
            return rec.content.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def content_bytes(self, rel_path: str) -> bytes | None:
        rec = self._records.get(rel_path)
        if rec is None:
            return None
        return rec.content

    def mtime(self, rel_path: str) -> datetime | None:
        rec = self._records.get(rel_path)
        return rec.mtime if rec is not None else None

    def __len__(self) -> int:
        return len(self._records)


@dataclass(frozen=True)
class _SnapshotRecord:
    path: str
    mtime: datetime | None
    size: int
    content: bytes


# ---------------------------------------------------------------------------
# Pre-RED violation detection
# ---------------------------------------------------------------------------


def detect_pre_red_violations(
    *,
    snapshot: WorkspaceSnapshot,
    classifier: PathClassifier,
    red_captured_at: datetime,
) -> tuple[PreRedViolation, ...]:
    """Return every pre-RED violation.

    A pre-RED violation is a path the classifier labels as
    ``production`` (or ``other``) whose **current on-disk mtime**
    differs from the snapshot mtime AND the change happened
    before ``red_captured_at``.

    Semantics: by the time the controller wrote the RED evidence,
    was there already an unauthorized production change? If the
    current on-disk file matches the snapshot, the task didn't
    touch it. If it differs and the change happened before RED
    capture, that's a violation.
    """
    violations: list[PreRedViolation] = []
    for rel in snapshot.paths:
        cls = classifier.classify(rel)
        if cls in ("test", "execution_artifact"):
            continue
        if cls not in ("production", "other"):
            continue
        snap_mtime = snapshot.mtime(rel)
        if snap_mtime is None:
            continue
        current_path = Path(snapshot.root) / rel
        if not current_path.exists():
            continue
        try:
            current_mtime = datetime.fromtimestamp(current_path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if current_mtime == snap_mtime:
            continue
        if current_mtime < red_captured_at:
            violations.append(
                PreRedViolation(
                    path=rel,
                    classification=cls,
                    reason=(
                        f"file changed at {current_mtime.isoformat()}, "
                        f"before RED capture at {red_captured_at.isoformat()}"
                    ),
                )
            )
    return tuple(violations)


# ---------------------------------------------------------------------------
# Unauthorized-change reverter
# ---------------------------------------------------------------------------


def revert_unauthorized(
    repo_root: Path,
    snapshot: WorkspaceSnapshot,
    rule: PathAuthorizationRule,
) -> tuple[Path, ...]:
    """Revert file content changes outside the rule's allowed paths.

    For each path in the snapshot:
    - If the file still exists and is authorized \u2192 leave alone.
    - If the file still exists and is NOT authorized \u2192 restore from
      snapshot content.
    - If the file was deleted \u2192 ignore (deletions of authorized files
      are NOT restored by design; tests pin this behaviour).
    - If the file is unauthorized and binary content was captured
      \u2192 write bytes back via ``Path.write_bytes``.

    Returns the paths that were reverted.
    """
    reverted: list[Path] = []
    for rel_posix in snapshot.paths:
        full = repo_root / rel_posix
        if not full.exists():
            # Deleted \u2014 see module docstring for the no-restore policy.
            continue
        if rule.is_authorized(rel_posix):
            continue
        content = snapshot.content_bytes(rel_posix)
        if content is None:
            raise RuntimeError(f"snapshot is missing content for {rel_posix}; cannot revert safely")
        # WP5 (story I): only revert when the on-disk content differs
        # from the snapshot. The pre-PR5 implementation reverted every
        # unauthorized file unconditionally, which made the
        # ``result.violations`` list noisy with no-op writes and made
        # the orchestrator's "unauthorized changes block delivery"
        # check misfire on legitimate pre-existing files outside
        # ``allowed_paths``. The semantic the SPEC requires is
        # "unauthorized *changes* block delivery" — unchanged files
        # outside ``allowed_paths`` are a workflow configuration
        # problem, not a security violation.
        current = full.read_bytes()
        if current == content:
            continue
        full.write_bytes(content)
        reverted.append(full)
    return tuple(reverted)


__all__ = [
    "PathClassifier",
    "PreRedViolation",
    "WorkspaceSnapshot",
    "detect_pre_red_violations",
    "revert_unauthorized",
]
