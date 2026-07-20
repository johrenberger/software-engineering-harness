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


class IdempotencyKeyConflictError(ValueError):
    """Raised when ``record_start`` is called with an ``idempotency_key``
    that already maps to a *different* ``run_id``.

    Cluster E1: callers pass a stable key for the same logical request;
    the ledger dedupes by that key. If two distinct run_ids collide on
    the same key, the caller is doing something wrong and we refuse
    rather than silently overwrite — they need to pick a fresh key.
    """


class RunState(StrEnum):
    """State of a feature run in the ledger.

    Cluster A adds ``BLOCKED = "blocked"`` for runs that hit a policy
    violation requiring human intervention (e.g. unauthorized file
    changes detected after remediation). ``PAUSED`` is awaiting an
    external signal (resume / approval); ``BLOCKED`` is a permanent
    policy halt that cannot be auto-resumed.
    """

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class RunRecord(BaseModel):
    """A single run record. Frozen.

    Cluster E1 (story E1): carries an optional ``idempotency_key``
    that callers can set to a stable identifier for their logical
    request (e.g. ``gh-<pr-number>`` or ``claude-session-<uuid>``).
    When the same key is presented twice, ``RunLedger.record_start``
    returns the existing record instead of creating a duplicate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    state: RunState
    repository: str = Field(min_length=1)
    started_at: str = Field(default_factory=lambda: _utcnow().isoformat())
    idempotency_key: str = Field(default="")


class RunLedger:
    """In-memory run ledger.

    Public mutators: ``record_start``, ``mark_resume``, ``mark_complete``,
    ``mark_cancelled``, ``mark_failed``. Read accessors: ``runs``,
    ``get``, ``__contains__``.

    Cluster E1: ``record_start`` accepts an optional ``idempotency_key``.
    - Same key + same ``run_id`` → returns the existing record (dedupe).
    - Same key + *different* ``run_id`` → raises
      :class:`IdempotencyKeyConflictError` to surface caller bugs.
    - Empty key → no dedupe, fresh record each call (preserves prior
      behavior for callers who haven't opted in).
    """

    DEFAULT_MAX_RECORDS = 100

    def __init__(self, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        if max_records < 1:
            raise ValueError("max_records must be ≥ 1")
        self._records: dict[str, RunRecord] = {}
        self._order: list[str] = []
        # Cluster E1: reverse lookup from idempotency_key → run_id so
        # ``record_start`` can dedupe in O(1). The index is rebuilt
        # via ``_rebuild_index`` whenever a record is replaced so it
        # stays consistent with ``_records``.
        self._key_index: dict[str, str] = {}
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

    def record_start(self, run_id: str, *, repository: str, idempotency_key: str = "") -> RunRecord:
        if not run_id:
            raise ValueError("run_id must be non-empty")
        if not repository:
            raise ValueError("repository must be non-empty")
        # Cluster E1: idempotency-key dedupe. The key is checked first
        # so that a caller that has not yet wired the key (older call
        # sites) is unaffected: empty key → fall straight through to
        # the run-id-based path. A non-empty key that's already
        # registered to a *different* run_id is a hard error.
        if idempotency_key:
            existing_run_id = self._key_index.get(idempotency_key)
            if existing_run_id is not None:
                if existing_run_id == run_id:
                    return self._records[existing_run_id]
                raise IdempotencyKeyConflictError(
                    f"idempotency_key {idempotency_key!r} already maps to "
                    f"run_id {existing_run_id!r}, not the requested "
                    f"{run_id!r}; pick a fresh key."
                )
        rec = RunRecord(
            run_id=run_id,
            state=RunState.RUNNING,
            repository=repository,
            idempotency_key=idempotency_key,
        )
        if run_id in self._records:
            # Preserve prior semantics for run-id-only callers: replace.
            # Re-keying is allowed: if the caller now wants to associate
            # a key with this run, we register it in the index, freeing
            # any previous key mapping for this run_id first.
            old = self._records[run_id]
            if old.idempotency_key and old.idempotency_key != idempotency_key:
                self._key_index.pop(old.idempotency_key, None)
            self._records[run_id] = rec
        else:
            self._records[run_id] = rec
            self._order.append(run_id)
        if idempotency_key:
            self._key_index[idempotency_key] = run_id
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

    def mark_blocked(self, run_id: str) -> RunRecord | None:
        """Cluster A: transition a run to ``BLOCKED`` (policy halt)."""
        return self._update_state(run_id, RunState.BLOCKED)

    def _update_state(self, run_id: str, state: RunState) -> RunRecord | None:
        rec = self._records.get(run_id)
        if rec is None:
            return None
        updated = RunRecord(
            run_id=rec.run_id,
            state=state,
            repository=rec.repository,
            started_at=rec.started_at,
            idempotency_key=rec.idempotency_key,
        )
        self._records[run_id] = updated
        return updated

    def _evict(self) -> None:
        while len(self._order) > self._max_records:
            evicted = self._order.pop(0)
            record = self._records.pop(evicted, None)
            # Cluster E1: keep the key index consistent with _records.
            if record is not None and record.idempotency_key:
                self._key_index.pop(record.idempotency_key, None)


def _as_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "state": record.state.value,
        "repository": record.repository,
        "started_at": record.started_at,
    }
