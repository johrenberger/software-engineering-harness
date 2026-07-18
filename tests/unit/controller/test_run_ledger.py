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
