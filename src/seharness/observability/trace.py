"""Trace model + persistence (Cluster E, stories E5+E6).

The :class:`Trace` is an append-only JSONL event log persisted to
``<run_dir>/trace.jsonl``. Every event is a frozen Pydantic model
tagged by ``event_kind`` (a discriminated union over :class:`TraceEvent`)
and is redacted via :class:`SecretRedactor` before write so secrets
never leak to disk.

Events emitted today (extensible — future slices add
``model_request`` / ``tool_invocation`` / ``cost_record`` / etc.):

- :class:`PhaseStarted` — emitted when a phase begins.
- :class:`PhaseCompleted` — emitted when a phase returns OK / SKIPPED.
- :class:`PhaseFailed` — emitted when a phase returns FAILED / BLOCKED
  / PAUSED, or raises.
- :class:`ArtifactProduced` — emitted when a phase writes a file to
  ``<run_dir>/``.

The writer is crash-safe: every emitted line is fsync'd before the
next emit. Reopening with ``append=True`` continues the existing log
so resume-from-crash works without losing events.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from seharness.observability.redactor import SecretRedactor


def _utc_timestamp() -> float:
    """Unix timestamp; sortable, monotonic-ish for tests."""
    return time.time()


# ---------------------------------------------------------------------------
# TraceEvent — tagged union
# ---------------------------------------------------------------------------


class TraceEvent(BaseModel):
    """Base class for all trace events.

    Subclasses set :attr:`event_kind` to a stable string literal so
    :meth:`from_dict` can dispatch on the field when round-tripping
    from JSONL.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    timestamp: float = Field(default_factory=_utc_timestamp)
    #: Discriminator; concrete subclasses must override as a class var.
    event_kind: ClassVar[str] = "trace_event"

    @property
    def kind(self) -> str:
        """Convenience accessor for the discriminator (instance-level)."""
        return self.event_kind

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict including the discriminator.

        Adds ``kind`` (the discriminator) so JSONL readers can
        dispatch without inspecting the subclass type.
        """
        d = self.model_dump(mode="json")
        d["kind"] = self.event_kind
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TraceEvent:
        """Dispatch to the concrete subclass matching ``d['kind']``."""
        kind = d.get("kind")
        registry: dict[str, type[TraceEvent]] = {
            "phase_started": PhaseStarted,
            "phase_completed": PhaseCompleted,
            "phase_failed": PhaseFailed,
            "artifact_produced": ArtifactProduced,
        }
        target = registry.get(kind or "")
        if target is None:
            raise ValueError(f"unknown trace event kind: {kind!r}")
        # Strip the discriminator before validation; the subclass
        # does not store ``kind`` (only ``event_kind``).
        payload = {k: v for k, v in d.items() if k != "kind"}
        return target.model_validate(payload)


class PhaseStarted(TraceEvent):
    """Emitted at the beginning of a phase invocation."""

    event_kind: ClassVar[str] = "phase_started"
    attempt: int = 0


class PhaseCompleted(TraceEvent):
    """Emitted when a phase returns OK or SKIPPED."""

    event_kind: ClassVar[str] = "phase_completed"
    outcome: Literal["ok", "skipped"] = "ok"
    artifact_paths: tuple[str, ...] = Field(default_factory=tuple)
    detail: str = ""


class PhaseFailed(TraceEvent):
    """Emitted when a phase returns FAILED / BLOCKED / PAUSED or raises."""

    event_kind: ClassVar[str] = "phase_failed"
    outcome: Literal["failed", "blocked", "paused", "error"] = "failed"
    error: str = ""


class ArtifactProduced(TraceEvent):
    """Emitted when a phase writes a file under ``<run_dir>``."""

    event_kind: ClassVar[str] = "artifact_produced"
    path: str = Field(min_length=1)
    artifact_kind: str = "file"


# ---------------------------------------------------------------------------
# TraceWriter — append-only JSONL
# ---------------------------------------------------------------------------


class TraceWriter:
    """Append-only JSONL writer for :class:`TraceEvent` instances.

    The writer opens the file in append mode (``open(path, "a")``) so
    a resume-from-crash extends the existing log. Every line is
    fsync'd before the call returns, ensuring the OS buffer is
    flushed to disk even if the process is killed mid-run.

    The writer redacts string fields of every event via
    :class:`SecretRedactor` before write. Redaction is per-value
    (string fields only) so non-string metadata (timestamps,
    counters) is preserved exactly.
    """

    def __init__(
        self,
        *,
        path: Path | str,
        redactor: SecretRedactor | None = None,
        append: bool = True,
    ) -> None:
        self._path = Path(path)
        self._redactor = redactor or SecretRedactor()
        self._append = append
        self._fp: Any = None
        self._closed = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        self._fp = open(self._path, mode, encoding="utf-8")  # noqa: SIM115

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: TraceEvent) -> None:
        """Write ``event`` as one JSON object (one line) to the trace."""
        if self._closed:
            raise RuntimeError("TraceWriter is closed")
        payload = _scrub_event(event, self._redactor)
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        assert self._fp is not None
        self._fp.write(line + "\n")
        self._fp.flush()
        with contextlib.suppress(OSError):
            # fsync may fail on some filesystems (e.g. tmpfs on some
            # containers); the writer still calls flush(). We do not
            # raise — durability is best-effort but the event is
            # written.
            os.fsync(self._fp.fileno())

    def close(self) -> None:
        """Flush + close the underlying file handle. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._fp is not None:
            try:
                self._fp.flush()
            finally:
                self._fp.close()
                self._fp = None

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _scrub_event(event: TraceEvent, redactor: SecretRedactor) -> dict[str, Any]:
    """Return ``event.to_dict()`` with string values redacted."""
    raw = event.to_dict()
    scrubbed: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            scrubbed[key] = redactor.redact(value)
        elif isinstance(value, (list, tuple)):
            scrubbed[key] = [redactor.redact(v) if isinstance(v, str) else v for v in value]
        else:
            scrubbed[key] = value
    return scrubbed


# ---------------------------------------------------------------------------
# Trace — convenience factory
# ---------------------------------------------------------------------------


class Trace:
    """Factory helpers for the per-run trace file.

    The orchestrator normally calls :meth:`Trace.for_run` to obtain a
    writer at the canonical path ``<run_dir>/trace.jsonl``. This
    centralises the path convention so downstream tooling (dashboards,
    grep operators) always know where to look.
    """

    @staticmethod
    def for_run(*, run_id: str, run_dir: Path | str) -> TraceWriter:
        """Return a :class:`TraceWriter` rooted at ``<run_dir>/trace.jsonl``.

        ``run_id`` is accepted for symmetry with future per-run
        metadata; today the writer only needs the path.
        """
        del run_id  # reserved for future per-run lock file
        return TraceWriter(path=Path(run_dir) / "trace.jsonl")
