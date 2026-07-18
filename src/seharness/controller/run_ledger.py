"""RunLedger — in-memory record of feature runs.

Per SPEC §'21. OpenClaw packaging' — the controller persists
in-progress runs so /status, /runs, /resume, /cancel work without
re-querying the model.

**Invariants:**
- ``RunRecord`` is a frozen Pydantic BaseModel.
- ``RunState`` is a StrEnum (not Literal) for runtime branching.
- ``RunLedger`` is mutable via service methods only; ``runs`` is a
  tuple snapshot.
- Bounded to ``max_records`` (default 100).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunState(StrEnum):
    """State of a feature run in the ledger."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class RunRecord(BaseModel):
    """A single run record. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    state: RunState
    repository: str = Field(min_length=1)
    started_at: str = Field(default_factory=lambda: _utcnow().isoformat())


class RunLedger:
    """In-memory run ledger.

    Public mutators: ``record_start``, ``mark_resume``, ``mark_complete``,
    ``mark_cancelled``, ``mark_failed``. Read accessors: ``runs``,
    ``get``, ``__contains__``.
    """

    DEFAULT_MAX_RECORDS = 100

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        if max_records < 1:
            raise ValueError("max_records must be ≥ 1")
        self._records: dict[str, RunRecord] = {}
        self._order: list[str] = []
        self._max_records = max_records

    @property
    def max_records(self) -> int:
        return self._max_records

    @property
    def runs(self) -> tuple[RunRecord, ...]:
        return tuple(self._records[rid] for rid in self._order)

    @property
    def last_run_id(self) -> str | None:
        return self._order[-1] if self._order else None

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def __contains__(self, run_id: object) -> bool:
        return isinstance(run_id, str) and run_id in self._records

    def record_start(self, run_id: str, *, repository: str) -> RunRecord:
        if not run_id:
            raise ValueError("run_id must be non-empty")
        if not repository:
            raise ValueError("repository must be non-empty")
        rec = RunRecord(
            run_id=run_id,
            state=RunState.RUNNING,
            repository=repository,
        )
        if run_id in self._records:
            self._records[run_id] = rec
        else:
            self._records[run_id] = rec
            self._order.append(run_id)
        self._evict()
        return rec

    def mark_resume(self, run_id: str) -> RunRecord | None:
        return self._update_state(run_id, RunState.RUNNING)

    def mark_complete(self, run_id: str) -> RunRecord | None:
        return self._update_state(run_id, RunState.COMPLETE)

    def mark_cancelled(self, run_id: str) -> RunRecord | None:
        return self._update_state(run_id, RunState.CANCELLED)

    def mark_failed(self, run_id: str) -> RunRecord | None:
        return self._update_state(run_id, RunState.FAILED)

    def mark_paused(self, run_id: str) -> RunRecord | None:
        return self._update_state(run_id, RunState.PAUSED)

    def _update_state(self, run_id: str, state: RunState) -> RunRecord | None:
        rec = self._records.get(run_id)
        if rec is None:
            return None
        updated = RunRecord(
            run_id=rec.run_id,
            state=state,
            repository=rec.repository,
            started_at=rec.started_at,
        )
        self._records[run_id] = updated
        return updated

    def _evict(self) -> None:
        while len(self._order) > self._max_records:
            evicted = self._order.pop(0)
            self._records.pop(evicted, None)


def _as_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "state": record.state.value,
        "repository": record.repository,
        "started_at": record.started_at,
    }
