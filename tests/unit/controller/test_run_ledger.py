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
