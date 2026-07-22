"""RED: RunLedger — in-memory record of feature runs.

SPEC §'21. OpenClaw packaging' — the controller MUST persist
in-progress runs to a ledger so /status, /runs, /resume, /cancel
work without re-querying the model.

RED bullets covered:
- Recording a start creates a run record.
- /resume updates the run state.
- /cancel marks the run cancelled.
- RunLedger is frozen for read; mutable via service methods only.
- Run records are bounded (last 100 by default).
"""

from __future__ import annotations

import pytest

from seharness.controller import RunLedger, RunRecord, RunState
from seharness.controller.run_ledger import OptimisticConcurrencyError


def test_run_ledger_starts_empty() -> None:
    ledger = RunLedger()
    assert ledger.runs == ()
    assert ledger.last_run_id is None


def test_record_start_creates_record() -> None:
    ledger = RunLedger()
    record = ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    assert record.run_id == "run-1"
    assert record.state == RunState.RUNNING
    assert record.repository == "git@github.com:foo/bar.git"
    assert "run-1" in ledger


def test_record_start_duplicate_replaces() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo-1")
    ledger.record_start("run-1", repository="repo-2")  # overwrite
    record = ledger.get("run-1")
    assert record is not None
    assert record.repository == "repo-2"


def test_get_missing_returns_none() -> None:
    ledger = RunLedger()
    assert ledger.get("does-not-exist") is None


def test_mark_resume_updates_state() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo-1")
    ledger.mark_resume("run-1")
    assert ledger.get("run-1").state == RunState.RUNNING


def test_mark_complete_updates_state() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo-1")
    ledger.mark_complete("run-1")
    assert ledger.get("run-1").state == RunState.COMPLETE


def test_mark_cancelled_updates_state() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo-1")
    ledger.mark_cancelled("run-1")
    assert ledger.get("run-1").state == RunState.CANCELLED


def test_mark_unknown_run_does_not_raise() -> None:
    ledger = RunLedger()
    ledger.mark_complete("does-not-exist")  # no-op
    ledger.mark_cancelled("does-not-exist")
    ledger.mark_resume("does-not-exist")


def test_runs_returns_tuple_in_recency_order() -> None:
    ledger = RunLedger()
    ledger.record_start("a", repository="ra")
    ledger.record_start("b", repository="rb")
    ledger.record_start("c", repository="rc")
    runs = ledger.runs
    assert [r.run_id for r in runs] == ["a", "b", "c"]


def test_run_ledger_bounded_to_max_records() -> None:
    ledger = RunLedger(max_records=5)
    for i in range(10):
        ledger.record_start(f"run-{i:03d}", repository=f"r-{i}")
    assert len(ledger.runs) == 5
    # Newest 5 retained
    assert [r.run_id for r in ledger.runs] == [
        "run-005",
        "run-006",
        "run-007",
        "run-008",
        "run-009",
    ]


def test_run_record_is_frozen() -> None:
    rec = RunRecord(run_id="x", state=RunState.RUNNING, repository="repo")
    with pytest.raises(Exception):  # noqa: B017
        rec.state = RunState.CANCELLED  # type: ignore[misc]


def test_run_record_carries_started_at_iso() -> None:
    rec = RunRecord(run_id="x", state=RunState.RUNNING, repository="repo")
    # ISO-8601 timestamp string
    assert "T" in rec.started_at
    assert rec.started_at.endswith("Z") or "+" in rec.started_at


def test_ledger_contains_checks_by_id() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    assert "run-1" in ledger
    assert "missing" not in ledger


def test_run_state_values() -> None:
    assert RunState.RUNNING.value == "running"
    assert RunState.PENDING.value == "pending"
    assert RunState.COMPLETE.value == "complete"
    assert RunState.CANCELLED.value == "cancelled"
    assert RunState.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Cluster E1: idempotency keys on RunLedger
# ---------------------------------------------------------------------------


def test_record_start_accepts_idempotency_key() -> None:
    """``record_start`` accepts and persists an ``idempotency_key``."""
    ledger = RunLedger()
    record = ledger.record_start("run-1", repository="repo-1", idempotency_key="req-abc")
    assert record.idempotency_key == "req-abc"
    fetched = ledger.get("run-1")
    assert fetched is not None
    assert fetched.idempotency_key == "req-abc"


def test_idempotent_recall_returns_existing_record() -> None:
    """Same key + same run_id → returns the existing record; no eviction."""
    ledger = RunLedger()
    first = ledger.record_start("run-1", repository="repo-1", idempotency_key="req-abc")
    # Mark the run PAUSED to prove state survives the dedupe.
    ledger.mark_paused("run-1")
    second = ledger.record_start("run-1", repository="repo-1", idempotency_key="req-abc")
    # The dedupe path returns the existing record and does NOT
    # mutate state or started_at.
    assert second.state == RunState.PAUSED
    assert second.started_at == first.started_at


def test_idempotency_key_collision_raises_with_different_run_id() -> None:
    """Same key + *different* run_id \u2192 ``IdempotencyKeyConflictError``."""
    from seharness.controller.run_ledger import IdempotencyKeyConflictError

    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo", idempotency_key="req-abc")
    with pytest.raises(IdempotencyKeyConflictError) as exc_info:
        ledger.record_start("run-2", repository="repo", idempotency_key="req-abc")
    assert "req-abc" in str(exc_info.value)
    assert "run-1" in str(exc_info.value)
    assert "run-2" in str(exc_info.value)


def test_empty_idempotency_key_falls_through_to_run_id_path() -> None:
    """Empty key bypasses dedupe entirely (back-compat for non-key callers).

    Pre-E1 semantics for ``record_start`` were "replace on duplicate
    ``run_id``". The Cluster E1 change MUST NOT alter that contract
    when ``idempotency_key`` is empty.
    """
    ledger = RunLedger()
    a = ledger.record_start("run-1", repository="repo-1")
    b = ledger.record_start("run-1", repository="repo-2")  # overwrite
    assert a.idempotency_key == ""
    assert b.idempotency_key == ""
    assert b.run_id == a.run_id
    assert b.repository == "repo-2"  # overwrite took effect
    # Order in ``runs`` is unchanged — replace doesn't bump recency.
    assert ledger.last_run_id == "run-1"


def test_non_conflicting_keys_coexist() -> None:
    """Distinct keys on distinct run_ids are independent."""
    ledger = RunLedger()
    a = ledger.record_start("run-1", repository="r", idempotency_key="k-a")
    b = ledger.record_start("run-2", repository="r", idempotency_key="k-b")
    c = ledger.record_start("run-3", repository="r")  # no key
    assert a.run_id == "run-1" and b.run_id == "run-2" and c.run_id == "run-3"
    assert a.idempotency_key == "k-a"
    assert b.idempotency_key == "k-b"
    assert c.idempotency_key == ""


def test_idempotency_key_survives_state_transitions() -> None:
    """Marking complete / cancelled / failed must keep the key on the record."""
    ledger = RunLedger()
    ledger.record_start("run-1", repository="r", idempotency_key="req-xyz")
    ledger.mark_complete("run-1")
    rec = ledger.get("run-1")
    assert rec is not None
    assert rec.idempotency_key == "req-xyz"
    assert rec.state == RunState.COMPLETE


def test_eviction_cleans_key_index() -> None:
    """When ``_evict`` drops a record, its key mapping must also drop so a
    later ``record_start`` with the same key doesn't conflict with a
    stale, gone record."""
    ledger = RunLedger(max_records=2)
    # Fill the ledger.
    ledger.record_start("r1", repository="r", idempotency_key="k-A")
    ledger.record_start("r2", repository="r", idempotency_key="k-B")
    # r1 gets evicted by adding r3.
    ledger.record_start("r3", repository="r", idempotency_key="k-C")
    # ``k-A`` is now free; using it on a fresh run must NOT conflict.
    fresh = ledger.record_start("r4", repository="r", idempotency_key="k-A")
    assert fresh.run_id == "r4"
    assert fresh.idempotency_key == "k-A"
    assert ledger.get("r4") is fresh
    # r1 was evicted.
    assert ledger.get("r1") is None


def test_run_id_replace_releases_old_key() -> None:
    """Re-keying a run_id frees its previous key in the index.

    Inverse of the conflict check: if a caller previously registered
    ``run-1`` with key ``k-A`` and now calls ``record_start(run-1,
    idempotency_key=)`` with ``k-A`` removed (empty), the slot frees
    so another run_id can take ``k-A``.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="r", idempotency_key="k-A")
    # Caller replaces r1 with no key.
    ledger.record_start("r1", repository="r", idempotency_key="")
    # Now k-A is free for a different run_id.
    fresh = ledger.record_start("r2", repository="r", idempotency_key="k-A")
    assert fresh.run_id == "r2"
    assert fresh.idempotency_key == "k-A"


def test_run_id_rekey_with_new_key() -> None:
    """Re-keying with a *different* key succeeds and updates the index."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="r", idempotency_key="k-A")
    fresh = ledger.record_start("r1", repository="r", idempotency_key="k-B")
    assert fresh.idempotency_key == "k-B"
    # Old key k-A is free.
    other = ledger.record_start("r2", repository="r", idempotency_key="k-A")
    assert other.run_id == "r2"


def test_controller_idempotency_default_is_empty_string() -> None:
    """``record_start`` defaults its key to ``""`` (back-compat invariant)."""
    import inspect

    sig = inspect.signature(RunLedger.record_start)
    assert sig.parameters["idempotency_key"].default == ""


# ---------------------------------------------------------------------------
# Cluster E2: optimistic concurrency via revision + expected_state CAS
# ---------------------------------------------------------------------------


def test_record_starts_at_revision_one() -> None:
    """Fresh records have ``revision == 1``.

    Subsequent ``record_start`` calls bumping revision:
    - Same idempotency_key on same run_id → E1 dedupe, no write.
    - Different idempotency_key on same run_id (E1 re-keying) → +1.
    Pure dedupe (same key + same run_id) → unchanged.
    """
    ledger2 = RunLedger()
    ledger2.record_start("r1", repository="repo", idempotency_key="k-A")
    assert ledger2.get("r1").revision == 1
    rec2 = ledger2.record_start("r1", repository="repo", idempotency_key="k-A")
    assert rec2.revision == 1  # dedupe did not write
    # Re-keying (E1 contract: swap key) → bump to 2.
    ledger2.record_start("r1", repository="repo", idempotency_key="k-B")
    assert ledger2.get("r1").revision == 2
    # Subsequent dedupe on k-B → still 2.
    ledger2.record_start("r1", repository="repo", idempotency_key="k-B")
    assert ledger2.get("r1").revision == 2


def test_replace_bumps_revision() -> None:
    """E1's re-keying path (same run_id, fresh idempotency_key)
    MUST bump revision so holders of the old revision see the change."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo", idempotency_key="k-A")
    assert ledger.get("r1").revision == 1
    # Re-key with no key → bump to 2.
    ledger.record_start("r1", repository="repo")
    assert ledger.get("r1").revision == 2
    # Re-key with k-B → bump to 3.
    ledger.record_start("r1", repository="repo", idempotency_key="k-B")
    assert ledger.get("r1").revision == 3


def test_mark_bumps_revision_on_success() -> None:
    """Every successful ``mark_*`` bumps revision by 1."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    assert ledger.get("r1").revision == 1
    ledger.mark_paused("r1")
    assert ledger.get("r1").revision == 2
    assert ledger.get("r1").state == RunState.PAUSED
    ledger.mark_resume("r1")
    assert ledger.get("r1").revision == 3
    ledger.mark_complete("r1")
    assert ledger.get("r1").revision == 4


def test_mark_with_matching_revision_succeeds() -> None:
    """``mark_complete(expected_revision=2)`` succeeds when revision
    is currently 2 → returns revision 3."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_paused("r1")
    result = ledger.mark_complete("r1", expected_revision=2)
    assert result is not None
    assert result.revision == 3
    assert result.state == RunState.COMPLETE


def test_mark_with_stale_revision_raises_actual_includes_obs() -> None:
    """``mark_complete(expected_revision=1)`` on a record at revision
    2 raises ``OptimisticConcurrencyError`` carrying observed values."""
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_paused("r1")
    with pytest.raises(OptimisticConcurrencyError) as exc_info:
        ledger.mark_complete("r1", expected_revision=1)
    err = exc_info.value
    assert err.run_id == "r1"
    assert err.expected_revision == 1
    assert err.actual_revision == 2
    # Ledger state was NOT mutated by the failed CAS.
    assert ledger.get("r1").state == RunState.PAUSED
    assert ledger.get("r1").revision == 2


def test_mark_with_matching_expected_state_succeeds() -> None:
    """``mark_complete(expected_state=RunState.RUNNING)`` succeeds
    when the record is currently RUNNING (the typical happy path)."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    result = ledger.mark_complete("r1", expected_state=RunState.RUNNING)
    assert result is not None
    assert result.state == RunState.COMPLETE
    assert result.revision == 2


def test_mark_with_stale_expected_state_raises() -> None:
    """``mark_resume(expected_state=RUNNING)`` on a PAUSED record raises."""
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_paused("r1")
    with pytest.raises(OptimisticConcurrencyError) as exc_info:
        ledger.mark_resume("r1", expected_state=RunState.RUNNING)
    err = exc_info.value
    assert err.expected_state == RunState.RUNNING
    assert err.actual_state == RunState.PAUSED
    # Ledger state untouched.
    assert ledger.get("r1").state == RunState.PAUSED


def test_mark_requires_both_cas_clauses_when_both_supplied() -> None:
    """If BOTH ``expected_revision`` and ``expected_state`` are
    supplied, BOTH must match. Mismatching one is enough to raise.
    """
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_paused("r1")
    # Both correct → success.
    ledger.mark_resume("r1", expected_revision=2, expected_state=RunState.PAUSED)
    assert ledger.get("r1").revision == 3
    assert ledger.get("r1").state == RunState.RUNNING
    # Revision matches, state doesn't → CAS fails.
    with pytest.raises(OptimisticConcurrencyError):
        ledger.mark_complete("r1", expected_revision=3, expected_state=RunState.PAUSED)
    # State matches, revision doesn't → CAS fails.
    with pytest.raises(OptimisticConcurrencyError):
        ledger.mark_complete("r1", expected_revision=2, expected_state=RunState.RUNNING)


def test_mark_without_cas_args_preserves_pre_e2_behavior() -> None:
    """``mark_*`` called with no ``expected_*`` args must keep the
    pre-E2 unconditional-transition semantics. Back-compat invariant.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    assert ledger.mark_paused("r1").revision == 2
    assert ledger.mark_resume("r1").revision == 3


def test_mark_unknown_run_id_returns_none_silently() -> None:
    """Pre-E2 invariant: ``mark_*`` on an unknown ``run_id`` returns
    ``None`` without raising. The CAS variant MUST preserve this."""
    ledger = RunLedger()
    assert ledger.mark_paused("does-not-exist") is None
    assert (
        ledger.mark_complete(
            "does-not-exist",
            expected_revision=1,
            expected_state=RunState.RUNNING,
        )
        is None
    )


def test_cas_loses_to_concurrent_modification() -> None:
    """Realistic scenario: two readers, one wins the CAS, the other
    retries. After losing the CAS the caller reads the new value and
    gets back in sync.
    """
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    snapshot_a = ledger.get("r1")
    ledger.mark_paused("r1")  # → rev 2
    with pytest.raises(OptimisticConcurrencyError) as exc:
        ledger.mark_resume("r1", expected_revision=snapshot_a.revision)
    assert exc.value.actual_revision == 2
    fresh = ledger.get("r1")
    assert fresh.revision == 2
    ledger.mark_resume("r1", expected_revision=fresh.revision, expected_state=RunState.PAUSED)
    assert ledger.get("r1").state == RunState.RUNNING
    assert ledger.get("r1").revision == 3


def test_idempotent_state_mark_also_bumps_revision() -> None:
    """``mark_complete(expected_state=RunState.COMPLETE)`` on an
    already-COMPLETE record: the CAS check passes (state matches)
    but the revision is incremented. This keeps ``revision`` strictly
    monotonic and visible to anyone watching.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_complete("r1")
    again = ledger.mark_complete("r1", expected_revision=2, expected_state=RunState.COMPLETE)
    assert again is not None
    assert again.revision == 3
    assert again.state == RunState.COMPLETE


def test_optimistic_concurrency_error_attributes() -> None:
    """The exception's attributes are populated and the message names
    each value so log scrubbers can grep them."""
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.mark_paused("r1")
    with pytest.raises(OptimisticConcurrencyError) as exc:
        ledger.mark_complete("r1", expected_revision=1)
    assert exc.value.run_id == "r1"
    assert exc.value.expected_revision == 1
    assert exc.value.actual_revision == 2
    assert exc.value.expected_state is None
    assert exc.value.actual_state == RunState.PAUSED
    msg = str(exc.value)
    assert "r1" in msg
    assert "revision" in msg.lower()


def test_revision_field_on_run_record_default() -> None:
    """``RunRecord.revision`` defaults to 1 (Pydantic invariant)."""
    rec = RunRecord(
        run_id="x",
        state=RunState.RUNNING,
        repository="r",
    )
    assert rec.revision == 1


# ---------------------------------------------------------------------------
# Cluster E3: state model — phase + ctx + feature_description persistence
# ---------------------------------------------------------------------------


def test_e3_default_phase_ctx_feature_description() -> None:
    """Fresh RunRecord has ``phase=None``, ``ctx=None``,
    ``feature_description=None``. Back-compat: existing callers
    that don't pass E3 fields see no change.
    """
    rec = RunRecord(
        run_id="r1",
        state=RunState.RUNNING,
        repository="repo",
    )
    assert rec.phase is None
    assert rec.ctx is None
    assert rec.feature_description is None


def test_e3_to_jsonable_passes_through_primitives() -> None:
    """``to_jsonable`` returns primitives unchanged."""
    from seharness.controller.run_ledger import to_jsonable

    assert to_jsonable(42) == 42
    assert to_jsonable("hello") == "hello"
    assert to_jsonable(None) is None
    assert to_jsonable([1, 2, 3]) == [1, 2, 3]


def test_e3_to_jsonable_coerces_pydantic_model() -> None:
    """``to_jsonable`` calls ``model_dump()`` on Pydantic models."""
    from pydantic import BaseModel

    from seharness.controller.run_ledger import to_jsonable

    class Thing(BaseModel):
        x: int
        name: str

    t = Thing(x=42, name="alpha")
    assert to_jsonable(t) == {"x": 42, "name": "alpha"}


def test_e3_to_jsonable_walks_lists_and_dicts() -> None:
    """``to_jsonable`` recurses into containers so nested Pydantic
    models are also coerced (a common case: ``ctx.task_results``
    containing a list of Pydantic task objects).
    """
    from pydantic import BaseModel

    from seharness.controller.run_ledger import to_jsonable

    class Item(BaseModel):
        v: int

    payload = {
        "items": [Item(v=1), Item(v=2)],
        "scalar": 7,
        "nested": {"inner": Item(v=99)},
    }
    out = to_jsonable(payload)
    assert out == {
        "items": [{"v": 1}, {"v": 2}],
        "scalar": 7,
        "nested": {"inner": {"v": 99}},
    }


def test_e3_record_phase_default_revision_bumps() -> None:
    """``record_phase`` advances revision and persists phase + ctx
    (state stays RUNNING)."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo", feature_description="feat")
    assert ledger.get("r1").revision == 1
    result = ledger.record_phase("r1", phase="implementation", ctx={"task_results": [{"id": 1}]})
    assert result is not None
    assert result.phase == "implementation"
    assert result.ctx == {"task_results": [{"id": 1}]}
    assert result.state == RunState.RUNNING
    assert result.revision == 2


def test_e3_record_phase_empty_phase_raises() -> None:
    """Defensive: empty phase string is rejected so the ledger
    never stores a meaningless cursor.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    with pytest.raises(ValueError, match="phase"):
        ledger.record_phase("r1", phase="")


def test_e3_record_phase_non_dict_ctx_raises() -> None:
    """``ctx`` must be a dict (or None). Lists / scalars are rejected
    so the JSONL envelope shape stays predictable.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    with pytest.raises(ValueError, match="ctx"):
        ledger.record_phase("r1", phase="implementation", ctx=["not", "a", "dict"])


def test_e3_record_phase_with_expected_revision_succeeds() -> None:
    """``record_phase(expected_revision=N)`` honours the E2 CAS check."""
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    # Revision 1 → record_phase advances to 2.
    result = ledger.record_phase("r1", phase="implementation", expected_revision=1)
    assert result is not None
    assert result.revision == 2


def test_e3_record_phase_with_stale_expected_revision_raises() -> None:
    """Stale ``expected_revision`` raises OptimisticConcurrencyError
    so concurrent writers can't clobber each other.
    """
    from seharness.controller.run_ledger import OptimisticConcurrencyError

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.record_phase("r1", phase="implementation")  # → rev 2
    with pytest.raises(OptimisticConcurrencyError):
        ledger.record_phase("r1", phase="validation", expected_revision=1)
    # Ledger state untouched.
    assert ledger.get("r1").phase == "implementation"


def test_e3_mark_complete_preserves_phase_and_ctx() -> None:
    """``mark_complete`` (an E2 transition) MUST preserve ``phase``
    + ``ctx`` set by ``record_phase`` so the cursor survives
    terminal-state transitions (for audit + replay-from-scratch).
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.record_phase("r1", phase="completed", ctx={"plan_id": 99})
    final = ledger.mark_complete("r1")
    assert final is not None
    assert final.state == RunState.COMPLETE
    assert final.phase == "completed"
    assert final.ctx == {"plan_id": 99}


def test_e3_record_start_replaces_phase_preserves_persisted() -> None:
    """E1's re-keying path (record_start on an existing run_id)
    must NOT wipe the persisted ``phase`` + ``ctx`` (E3) — a
    re-record should be a write, not a reset. ``feature_description``
    follows the E3 semantics: new value wins if supplied,
    otherwise the prior value is preserved.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo", feature_description="first")
    ledger.record_phase("r1", phase="validation", ctx={"exit_code": 0})
    # Re-record with no description → prior one preserved; phase + ctx
    # also preserved (was previously lossy on E1's replace path).
    again = ledger.record_start("r1", repository="repo")
    assert again.feature_description == "first"
    assert again.phase == "validation"
    assert again.ctx == {"exit_code": 0}
    assert again.revision == 3  # 1 (start) → 2 (record_phase) → 3 (re-record)


def test_e3_record_start_feature_description_override() -> None:
    """When a re-record supplies a new ``feature_description``, the
    new value wins. ``phase`` + ``ctx`` are still preserved.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo", feature_description="v1")
    ledger.record_phase("r1", phase="implementation")
    again = ledger.record_start("r1", repository="repo", feature_description="v2-override")
    assert again.feature_description == "v2-override"
    assert again.phase == "implementation"


def test_e3_run_record_phase_field_in_asdict_payload() -> None:
    """``_as_dict`` includes phase + ctx + feature_description when
    they're set, omits them when None (keeps the on-disk format
    terse for callers that haven't wired E3).
    """
    from seharness.controller.run_ledger import _as_dict

    base = RunRecord(run_id="r1", state=RunState.RUNNING, repository="repo")
    payload = _as_dict(base)
    assert "phase" not in payload
    assert "ctx" not in payload
    assert "feature_description" not in payload

    full = RunRecord(
        run_id="r1",
        state=RunState.RUNNING,
        repository="repo",
        phase="implementation",
        ctx={"x": 1},
        feature_description="feat",
    )
    payload_full = _as_dict(full)
    assert payload_full["phase"] == "implementation"
    assert payload_full["ctx"] == {"x": 1}
    assert payload_full["feature_description"] == "feat"


# ---------------------------------------------------------------------------
# Cluster P3: cost-attribution on the run record
# ---------------------------------------------------------------------------
"""Cluster P3: cost-attribution fields on the audit trail.

Pins the deferred follow-up: cost-attribution fields on the run
record so the dashboard / audit trail can surface "how much did
this run cost?" without re-running anything.

Coverage:

- ``record_cost_attribution`` stamps the four fields on an
  existing record and bumps revision (Cluster E2 invariants).
- ``None`` arguments leave the existing value alone (partial
  update).
- Negative values are rejected at the Pydantic layer.
- Missing run_id returns ``None`` (mirrors ``mark_*`` family).
- Cluster E2 CAS: stale ``expected_revision`` raises
  ``OptimisticConcurrencyError`` before mutation.
- Re-keying on ``record_start`` does NOT wipe prior cost-
  attribution (mirrors the phase + ctx preservation).
- ``by_task`` accepts the per-task breakdown shape that
  matches the Cluster P2 ``<run_dir>/budget/by-task.json``
  artifact (outer ``task_id``, inner axis names).
"""


def test_p3_record_cost_attribution_stamps_totals() -> None:
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    rec = ledger.record_cost_attribution(
        "r1",
        total_tokens=5000,
        total_cost_usd=0.012,
        total_elapsed_s=4.5,
    )
    assert rec is not None
    assert rec.total_tokens == 5000
    assert rec.total_cost_usd == 0.012
    assert rec.total_elapsed_s == 4.5
    assert rec.by_task is None
    # Revision bumped exactly once.
    assert rec.revision == 2


def test_p3_record_cost_attribution_stamps_by_task() -> None:
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    by_task = {
        "task-foo": {"model_tokens": 100.0, "model_cost_usd": 0.003},
        "task-bar": {"model_tokens": 250.0, "model_cost_usd": 0.007},
    }
    rec = ledger.record_cost_attribution("r1", by_task=by_task)
    assert rec is not None
    assert rec.by_task == by_task
    # Totals still None when only by_task is provided.
    assert rec.total_tokens is None
    assert rec.total_cost_usd is None


def test_p3_record_cost_attribution_partial_update() -> None:
    """A later call can update only some fields; ``None``
    means "leave the existing value alone" (the common case
    where the orchestrator records totals first and then the
    per-task breakdown once the final phase boundary fires).
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    ledger.record_cost_attribution("r1", total_tokens=1000, total_cost_usd=0.005)
    # Second call updates by_task + total_elapsed_s; the prior
    # totals stay intact.
    rec = ledger.record_cost_attribution(
        "r1",
        by_task={"task-x": {"model_tokens": 500.0}},
        total_elapsed_s=2.5,
    )
    assert rec is not None
    assert rec.total_tokens == 1000  # preserved
    assert rec.total_cost_usd == 0.005  # preserved
    assert rec.total_elapsed_s == 2.5  # updated
    assert rec.by_task == {"task-x": {"model_tokens": 500.0}}  # updated


def test_p3_record_cost_attribution_unknown_run_returns_none() -> None:
    ledger = RunLedger()
    assert ledger.record_cost_attribution("ghost", total_tokens=1) is None


def test_p3_record_cost_attribution_cas_stale_revision_raises() -> None:
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")  # revision 1
    ledger.record_phase("r1", phase="spec")  # revision 2
    # Stale CAS.
    with pytest.raises(OptimisticConcurrencyError):
        ledger.record_cost_attribution(
            "r1",
            total_tokens=100,
            expected_revision=1,
        )
    # Ledger untouched: revision still 2, totals still None.
    rec = ledger.get("r1")
    assert rec is not None
    assert rec.revision == 2
    assert rec.total_tokens is None


def test_p3_record_cost_attribution_cas_matching_revision_succeeds() -> None:
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")  # revision 1
    ledger.record_phase("r1", phase="spec")  # revision 2
    rec = ledger.record_cost_attribution(
        "r1",
        total_tokens=2500,
        expected_revision=2,
    )
    assert rec is not None
    assert rec.revision == 3
    assert rec.total_tokens == 2500


def test_p3_record_cost_attribution_rejects_negative_totals() -> None:
    """Negative cost-attribution is a programmer error --
    the underlying :class:`BudgetTracker` records ``ge=0`` so
    a negative value would never legitimately reach the
    ledger. Cluster P3 raises at the Pydantic boundary
    rather than silently clamping.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunRecord(
            run_id="r1",
            state=RunState.RUNNING,
            repository="repo",
            total_tokens=-1,
        )
    with pytest.raises(ValidationError):
        RunRecord(
            run_id="r1",
            state=RunState.RUNNING,
            repository="repo",
            total_cost_usd=-0.01,
        )
    with pytest.raises(ValidationError):
        RunRecord(
            run_id="r1",
            state=RunState.RUNNING,
            repository="repo",
            total_elapsed_s=-1.0,
        )


def test_p3_record_cost_attribution_defaults_to_none() -> None:
    """Pre-P3 record construction (or any record that never
    had cost-attribution stamped) carries ``None`` for all
    four fields, not ``0``. ``0`` would be a real value
    (a run that consumed zero model axes); ``None`` means
    "not recorded" so the dashboard can render ``n/a``
    instead of a misleading ``0``.
    """
    rec = RunRecord(run_id="r1", state=RunState.RUNNING, repository="repo")
    assert rec.total_tokens is None
    assert rec.total_cost_usd is None
    assert rec.total_elapsed_s is None
    assert rec.by_task is None


def test_p3_record_start_rekey_preserves_cost_attribution() -> None:
    """Cluster E1's re-keying contract preserves cost-
    attribution. A re-``record_start`` on the same ``run_id``
    bumps revision but keeps the prior cost-attribution
    intact so the audit trail doesn't lose data on a
    caller retry.
    """
    ledger = RunLedger()
    ledger.record_start("r1", repository="repo", idempotency_key="k1")
    ledger.record_cost_attribution(
        "r1",
        total_tokens=1000,
        total_cost_usd=0.01,
    )
    # Re-key (same run_id, different idempotency_key).
    rec = ledger.record_start(
        "r1",
        repository="repo",
        idempotency_key="k2-override",
    )
    assert rec.total_tokens == 1000
    assert rec.total_cost_usd == 0.01


def test_p3_by_task_accepts_cluster_p2_artifact_shape() -> None:
    """The ``by_task`` shape intentionally matches the
    Cluster P2 ``<run_dir>/budget/by-task.json`` envelope
    (outer ``task_id``, inner ``{model_tokens,
    model_cost_usd, elapsed_seconds}``). A roundtrip
    through the recorder's persistence path is the
    end-to-end contract.
    """
    from collections.abc import Mapping

    from seharness.orchestrator.budgets import BudgetAxis, RunBudgets
    from seharness.orchestrator.per_phase_budget import (
        build_recorder,
    )

    budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
    recorder = build_recorder(budgets=budgets)
    recorder.record_invocation(
        phase_id="spec",
        task_id="task-foo",
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.006,
        elapsed_s=0.5,
    )
    recorder.record_invocation(
        phase_id="implement",
        task_id="task-foo",
        input_tokens=250,
        output_tokens=100,
        cost_usd=0.007,
        elapsed_s=1.0,
    )
    by_task: Mapping[str, Mapping[BudgetAxis, float]] = recorder.consumption_by_task()

    # Convert axis-keyed mapping to the string-keyed shape the
    # ledger expects.
    ledger_payload = {
        task_id: {axis.value: amount for axis, amount in axes.items()}
        for task_id, axes in by_task.items()
    }

    ledger = RunLedger()
    ledger.record_start("r1", repository="repo")
    rec = ledger.record_cost_attribution("r1", by_task=ledger_payload)
    assert rec is not None
    assert rec.by_task == ledger_payload
    # The task accumulated across two phases.
    assert rec.by_task["task-foo"]["model_tokens"] == 650.0
    assert abs(rec.by_task["task-foo"]["model_cost_usd"] - 0.013) < 1e-9


def test_p3_recorder_underlying_totals_match_attribution() -> None:
    """When the per-task breakdown is stamped on the ledger,
    the sum of ``by_task`` MUST equal the recorder's
    underlying ``consumption()`` totals (sanity check that
    the recorder and the ledger agree on the numbers).
    """
    from seharness.orchestrator.budgets import BudgetAxis, RunBudgets
    from seharness.orchestrator.per_phase_budget import build_recorder

    budgets = RunBudgets(model_tokens=10_000, model_cost_usd=1.0)
    recorder = build_recorder(budgets=budgets)
    recorder.record_invocation(
        phase_id="spec",
        task_id="task-foo",
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.006,
        elapsed_s=0.5,
    )
    recorder.record_invocation(
        phase_id="implement",
        task_id="task-bar",
        input_tokens=250,
        output_tokens=100,
        cost_usd=0.007,
        elapsed_s=1.0,
    )
    by_task = {
        task_id: {axis.value: amount for axis, amount in axes.items()}
        for task_id, axes in recorder.consumption_by_task().items()
    }
    underlying = recorder.tracker.consumption()
    sum_tokens = sum(t.get("model_tokens", 0.0) for t in by_task.values())
    sum_cost = sum(t.get("model_cost_usd", 0.0) for t in by_task.values())
    assert sum_tokens == underlying[BudgetAxis.MODEL_TOKENS]
    assert sum_cost == underlying[BudgetAxis.MODEL_COST_USD]
