"""Observability primitives (Cluster E, stories E5+E6).

The ``observability`` package provides:

- :class:`seharness.observability.redactor.SecretRedactor` — scrubs common
  secret patterns from arbitrary text and dicts before persistence.
- :class:`seharness.observability.trace.TraceWriter` — append-only JSONL
  writer at ``<run_dir>/trace.jsonl`` with per-line fsync.
- :class:`seharness.observability.trace.TraceEvent` and its concrete
  subclasses (:class:`PhaseStarted`, :class:`PhaseCompleted`,
  :class:`PhaseFailed`, :class:`ArtifactProduced`) — frozen Pydantic
  models tagged by ``kind`` for discriminated union round-trips.

The orchestrator (slice-7+) emits these events through an injected
``TraceWriter`` so tests can substitute an in-memory recorder and
operators can disable tracing entirely (``trace_writer=None``).
"""

from __future__ import annotations

from seharness.observability.redactor import (
    REDACTION_SENTINEL,
    SecretRedactor,
)
from seharness.observability.trace import (
    ArtifactProduced,
    PhaseCompleted,
    PhaseFailed,
    PhaseStarted,
    Trace,
    TraceEvent,
    TraceWriter,
)

__all__ = [
    "REDACTION_SENTINEL",
    "ArtifactProduced",
    "PhaseCompleted",
    "PhaseFailed",
    "PhaseStarted",
    "SecretRedactor",
    "Trace",
    "TraceEvent",
    "TraceWriter",
]
