"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 5.

'Duplicate resume does not create duplicate commits or PRs':
- IdempotencyKey uniquely identifies a (run_id, task_id) commit/PR pair.
- IdempotencyStore is file-based JSON, persisted across process restarts.
- Re-running with the same key returns the prior commit/PR state
  without creating new artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.delivery.idempotency import (
    IdempotencyKey,
    IdempotencyRecord,
    IdempotencyStore,
)


def _store(tmp_path: Path) -> IdempotencyStore:
    return IdempotencyStore(root=tmp_path / "idem")


def test_key_string_format_includes_run_and_task() -> None:
    key = IdempotencyKey(run_id="R-1", task_id="T-1")
    assert "R-1" in key.as_filename()
    assert "T-1" in key.as_filename()


def test_same_inputs_produce_same_key() -> None:
    a = IdempotencyKey(run_id="R-1", task_id="T-1")
    b = IdempotencyKey(run_id="R-1", task_id="T-1")
    assert a == b
    assert a.as_filename() == b.as_filename()


def test_different_runs_produce_different_keys() -> None:
    a = IdempotencyKey(run_id="R-1", task_id="T-1")
    b = IdempotencyKey(run_id="R-2", task_id="T-1")
    assert a != b


def test_store_get_returns_none_for_missing_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get(IdempotencyKey(run_id="R-1", task_id="T-1")) is None


def test_store_put_then_get_returns_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    key = IdempotencyKey(run_id="R-1", task_id="T-1")
    record = IdempotencyRecord(
        commit_sha="deadbeef",
        branch="agent/01-config",
        pr_url="https://github.com/x/y/pull/1",
    )
    store.put(key, record)
    fetched = store.get(key)
    assert fetched == record


def test_store_persists_across_reopen(tmp_path: Path) -> None:
    """Records MUST survive process restart (file-based JSON)."""
    store1 = _store(tmp_path)
    key = IdempotencyKey(run_id="R-1", task_id="T-1")
    record = IdempotencyRecord(commit_sha="abc123", branch="agent/x", pr_url=None)
    store1.put(key, record)

    # Reopen
    store2 = _store(tmp_path)
    assert store2.get(key) == record


def test_duplicate_put_is_idempotent(tmp_path: Path) -> None:
    """Putting the same key twice MUST NOT create two records."""
    store = _store(tmp_path)
    key = IdempotencyKey(run_id="R-1", task_id="T-1")
    r1 = IdempotencyRecord(commit_sha="abc123", branch="x", pr_url=None)
    r2 = IdempotencyRecord(commit_sha="abc123", branch="x", pr_url="https://github.com/x/y/pull/1")
    store.put(key, r1)
    store.put(key, r2)
    # Second put wins (record is the latest known state).
    assert store.get(key) == r2


def test_store_root_is_created_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "new-dir" / "idem"
    assert not root.exists()
    IdempotencyStore(root=root)
    assert root.exists()


def test_record_pr_url_is_optional() -> None:
    record = IdempotencyRecord(commit_sha="abc", branch="agent/x", pr_url=None)
    assert record.pr_url is None


def test_record_rejects_unknown_field() -> None:
    with pytest.raises(Exception):  # noqa: B017
        IdempotencyRecord(
            commit_sha="abc",
            branch="x",
            pr_url=None,
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_record_is_frozen() -> None:
    record = IdempotencyRecord(commit_sha="abc", branch="x", pr_url=None)
    with pytest.raises(Exception):  # noqa: B017
        record.commit_sha = "def"  # type: ignore[misc]


def test_store_handles_concurrent_distinct_keys(tmp_path: Path) -> None:
    """Different keys MUST be stored independently."""
    store = _store(tmp_path)
    k1 = IdempotencyKey(run_id="R-1", task_id="T-1")
    k2 = IdempotencyKey(run_id="R-1", task_id="T-2")
    store.put(k1, IdempotencyRecord(commit_sha="aaa", branch="x", pr_url=None))
    store.put(k2, IdempotencyRecord(commit_sha="bbb", branch="x", pr_url=None))
    assert store.get(k1).commit_sha == "aaa"
    assert store.get(k2).commit_sha == "bbb"
