"""RED: Operator pause/resume flow via OpenClaw skills.

SPEC §'21. OpenClaw packaging' RED bullet 2 — A run can be paused
and resumed by operator skill. The controller exposes a Pauser
Protocol and a default StubPauser that:
- records pause + resume calls per run-id,
- marks the RunLedger state accordingly,
- fails-secure if asked to pause/resume a missing run.

RED bullets covered:
- Pauser.pause(run_id) marks ledger state = PAUSED.
- Resumer.resume(run_id) marks ledger state = RUNNING.
- Pause of unknown run → returns failure dict, no exception.
- Resumer requires a non-empty run_id.
"""

from __future__ import annotations

import pytest

from seharness.controller import (
    Pauser,
    Resumer,
    RunLedger,
    RunState,
    StubPauser,
    StubResumer,
)


def test_pauser_marks_ledger_paused() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    pauser = StubPauser(ledger=ledger)
    result = pauser.pause("run-1")
    assert result["ok"] is True
    assert ledger.get("run-1").state == RunState.PAUSED


def test_resumer_marks_ledger_running() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    resumer = StubResumer(ledger=ledger)
    result = resumer.resume("run-1")
    assert result["ok"] is True
    assert ledger.get("run-1").state == RunState.RUNNING


def test_pauser_unknown_run_returns_failure() -> None:
    ledger = RunLedger()
    pauser = StubPauser(ledger=ledger)
    result = pauser.pause("missing")
    assert result["ok"] is False


def test_resumer_unknown_run_returns_failure() -> None:
    ledger = RunLedger()
    resumer = StubResumer(ledger=ledger)
    result = resumer.resume("missing")
    assert result["ok"] is False


def test_resumer_rejects_empty_run_id() -> None:
    ledger = RunLedger()
    resumer = StubResumer(ledger=ledger)
    with pytest.raises(ValueError, match=r"run_id"):
        resumer.resume("")


def test_pauser_rejects_empty_run_id() -> None:
    ledger = RunLedger()
    pauser = StubPauser(ledger=ledger)
    with pytest.raises(ValueError, match=r"run_id"):
        pauser.pause("")


def test_pause_then_resume_round_trip() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    pauser = StubPauser(ledger=ledger)
    resumer = StubResumer(ledger=ledger)
    pauser.pause("run-1")
    resumer.resume("run-1")
    assert ledger.get("run-1").state == RunState.RUNNING


def test_pauser_protocol_conformance() -> None:
    """StubPauser MUST satisfy the Pauser Protocol."""
    ledger = RunLedger()
    pauser: Pauser = StubPauser(ledger=ledger)
    ledger.record_start("run-1", repository="repo")
    result = pauser.pause("run-1")
    assert "ok" in result


def test_resumer_protocol_conformance() -> None:
    """StubResumer MUST satisfy the Resumer Protocol."""
    ledger = RunLedger()
    resumer: Resumer = StubResumer(ledger=ledger)
    ledger.record_start("run-1", repository="repo")
    result = resumer.resume("run-1")
    assert "ok" in result


def test_pauser_records_call_history() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    ledger.record_start("run-2", repository="repo")
    pauser = StubPauser(ledger=ledger)
    pauser.pause("run-1")
    pauser.pause("run-2")
    assert pauser.call_history == ("run-1", "run-2")


def test_resumer_records_call_history() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="repo")
    resumer = StubResumer(ledger=ledger)
    resumer.resume("run-1")
    assert resumer.call_history == ("run-1",)
