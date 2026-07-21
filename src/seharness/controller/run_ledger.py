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


class OptimisticConcurrencyError(ValueError):
    """Raised when a ``mark_*`` call detects the ledger has been
    mutated since the caller read it.

    Cluster E2: callers pass an ``expected_revision`` and/or
    ``expected_state`` with every state transition. The transition
    only fires if both expected values still match the current
    record. On mismatch the transition is rejected and the caller
    must re-read the record (the ``actual`` and ``expected`` values
    are surfaced for diagnostics).

    Carries ``run_id``, ``expected_revision``, ``actual_revision``,
    ``expected_state``, ``actual_state`` so callers can retry with
    fresh values.
    """

    def __init__(
        self,
        *,
        run_id: str,
        expected_revision: int | None,
        actual_revision: int,
        expected_state: RunState | None,
        actual_state: RunState,
    ) -> None:
        self.run_id = run_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.expected_state = expected_state
        self.actual_state = actual_state
        super().__init__(
            f"optimistic concurrency conflict on run_id {run_id!r}: "
            f"expected revision={expected_revision}, state={expected_state}; "
            f"actual revision={actual_revision}, state={actual_state}; "
            f"re-read the record and retry."
        )


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


def to_jsonable(value: Any) -> Any:
    """Coerce a value into a JSON-serializable form.

    Cluster E3: ``RunRecord.ctx`` MUST round-trip through JSON
    (the FileRunLedger persists it on the JSONL envelope). This
    helper handles the common case where ctx contains a Pydantic
    model (or a list / dict of them). Other values pass through
    unchanged — if a value isn't JSON-serializable the downstream
    ``json.dumps`` will raise with a clear error.

    Recognised transformations:
    - ``pydantic.BaseModel`` (and subclasses) → ``model_dump()``
    - ``list`` / ``tuple`` / ``set`` → recursively coerced
    - ``dict`` → recursively coerced (keys must be strings)
    - Anything else: returned as-is.
    """
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


class RunRecord(BaseModel):
    """A single run record. Frozen.

    Cluster E1 (story E1): carries an optional ``idempotency_key``
    that callers can set to a stable identifier for their logical
    request (e.g. ``gh-<pr-number>`` or ``claude-session-<uuid>``).
    When the same key is presented twice, ``RunLedger.record_start``
    returns the existing record instead of creating a duplicate.

    Cluster E2 (story E2): carries a monotonic ``revision`` integer.
    It is bumped on every state transition (``mark_*``) AND on every
    ``record_start`` ``run_id`` replacement (preserving the E1
    re-keying contract: a caller who read the record at revision N
    must CAS against N+1 after a re-key). New records start at 1.

    Cluster E3 (story E3): carries ``phase`` + ``ctx`` so a run can
    be resumed from its last successful phase across a process
    restart. ``phase`` is the name of the last completed phase (or
    ``None`` for a fresh run). ``ctx`` is the JSON-serializable
    orchestrator context accumulated by phase handlers; handlers
    must call :func:`to_jsonable` to coerce Pydantic models before
    assigning to ``ctx``. ``feature_description`` is also persisted
    so the resume seam can detect spec drift between the original
    run and the resume request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    state: RunState
    repository: str = Field(min_length=1)
    started_at: str = Field(default_factory=lambda: _utcnow().isoformat())
    idempotency_key: str = Field(default="")
    revision: int = Field(default=1, ge=1)
    # Cluster E3: persistence for cross-process resume.
    phase: str | None = Field(default=None)
    ctx: dict[str, Any] | None = Field(default=None)
    feature_description: str | None = Field(default=None)


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

    def record_start(
        self,
        run_id: str,
        *,
        repository: str,
        idempotency_key: str = "",
        feature_description: str | None = None,
    ) -> RunRecord:
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
            feature_description=feature_description,
            # New record: revision 1. On replace (same run_id) the
            # bumped revision is applied below so the E1 re-keying
            # path stays visible to holders of the old revision.
        )
        if run_id in self._records:
            # Preserve prior semantics for run-id-only callers: replace.
            # Re-keying is allowed: if the caller now wants to associate
            # a key with this run, we register it in the index, freeing
            # any previous key mapping for this run_id first. Revision
            # is bumped so optimistic-concurrency callers see the change.
            old = self._records[run_id]
            if old.idempotency_key and old.idempotency_key != idempotency_key:
                self._key_index.pop(old.idempotency_key, None)
            # Cluster E3: also carry over phase + ctx so a re-record
            # of the same run_id doesn't accidentally wipe the resume
            # state (E1's replace path was previously lossy on those
            # fields — we fix that here).
            rec = rec.model_copy(
                update={
                    "revision": old.revision + 1,
                    "phase": old.phase,
                    "ctx": old.ctx,
                    "feature_description": feature_description
                    if feature_description is not None
                    else old.feature_description,
                }
            )
            self._records[run_id] = rec
        else:
            self._records[run_id] = rec
            self._order.append(run_id)
        if idempotency_key:
            self._key_index[idempotency_key] = run_id
        self._evict()
        return rec

    # ----- Cluster E3: phase + ctx persistence ----------------------
    #
    # ``record_phase`` lets the orchestrator persist the resume cursor
    # (last-completed phase + accumulated context) in the same atomic
    # step as the state transition. ``ctx`` MUST be JSON-serializable
    # — callers should pass ``to_jsonable(ctx)`` if the dict contains
    # Pydantic models. The state stays ``RUNNING``; use ``mark_paused``
    # or ``mark_blocked`` to halt.

    def record_phase(
        self,
        run_id: str,
        *,
        phase: str,
        ctx: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> RunRecord | None:
        """Cluster E3: record the last-completed phase + updated ctx.

        ``phase`` is the name of the phase that just finished (or is
        currently in progress). ``ctx`` is the orchestrator context
        accumulated so far (JSON-serializable). The record's state
        stays ``RUNNING``; revision bumps on every call so concurrent
        callers see the change.
        """
        if not phase:
            raise ValueError("phase must be non-empty")
        if ctx is not None and not isinstance(ctx, dict):
            raise ValueError("ctx must be a dict (or None)")
        return self._update_state(
            run_id,
            RunState.RUNNING,
            expected_revision=expected_revision,
            phase=phase,
            ctx=ctx,
        )

    # ----- Cluster E2: optimistic concurrency ----------------------
    #
    # Every ``mark_*`` accepts:
    #   * ``expected_revision: int | None`` — CAS against the record's
    #     current ``revision``. On mismatch: ``OptimisticConcurrencyError``.
    #   * ``expected_state: RunState | None`` — semantic CAS against the
    #     record's current ``state``. On mismatch: ``OptimisticConcurrencyError``.
    # Either or both may be set; both must match if both are set. A ``None``
    # skips that particular check (the standard pre-E2 path).
    # ---------------------------``-----------------------------------

    def mark_resume(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.RUNNING,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_complete(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.COMPLETE,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_cancelled(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.CANCELLED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_failed(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.FAILED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_paused(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.PAUSED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_blocked(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        """Cluster A: transition a run to ``BLOCKED`` (policy halt)."""
        return self._update_state(
            run_id,
            RunState.BLOCKED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def _update_state(
        self,
        run_id: str,
        state: RunState,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
        phase: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> RunRecord | None:
        """Internal: apply a state transition with E2 CAS + E3 phase/ctx.

        The ``phase`` and ``ctx`` kwargs let callers (typically
        :meth:`record_phase`) update the resume cursor in the same
        atomic step as the state transition. When ``None`` the
        previous values are preserved (back-compat for callers that
        don't care about E3 persistence).
        """
        rec = self._records.get(run_id)
        if rec is None:
            return None
        # Cluster E2: optimistic-concurrency check. Both expected_*
        # MUST match when set. Failure raises BEFORE mutating so the
        # ledger state stays untouched (no half-applied transitions).
        if expected_revision is not None and expected_revision != rec.revision:
            raise OptimisticConcurrencyError(
                run_id=run_id,
                expected_revision=expected_revision,
                actual_revision=rec.revision,
                expected_state=expected_state,
                actual_state=rec.state,
            )
        if expected_state is not None and expected_state != rec.state:
            raise OptimisticConcurrencyError(
                run_id=run_id,
                expected_revision=expected_revision,
                actual_revision=rec.revision,
                expected_state=expected_state,
                actual_state=rec.state,
            )
        updated = RunRecord(
            run_id=rec.run_id,
            state=state,
            repository=rec.repository,
            started_at=rec.started_at,
            idempotency_key=rec.idempotency_key,
            revision=rec.revision + 1,
            # Cluster E3: carry forward + apply new values. ``phase``
            # and ``ctx`` default to the previous values when the
            # caller doesn't override them, so partial updates don't
            # wipe resume state.
            phase=phase if phase is not None else rec.phase,
            ctx=ctx if ctx is not None else rec.ctx,
            feature_description=rec.feature_description,
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
    """Serialise a RunRecord to a JSON-ready dict.

    Cluster E3: ``phase`` + ``ctx`` + ``feature_description`` are
    included when set, omitted otherwise (keeps the on-disk format
    terse for callers that haven't wired E3 yet).
    """
    payload: dict[str, Any] = {
        "run_id": record.run_id,
        "state": record.state.value,
        "repository": record.repository,
        "started_at": record.started_at,
    }
    if record.feature_description is not None:
        payload["feature_description"] = record.feature_description
    if record.phase is not None:
        payload["phase"] = record.phase
    if record.ctx is not None:
        payload["ctx"] = record.ctx
    return payload
