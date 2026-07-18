"""Pauser + Resumer Protocols and Stub implementations.

Per SPEC §'21. OpenClaw packaging' RED bullet 2 — operator can pause
and resume a run via OpenClaw skill. This module provides:
- ``Pauser`` Protocol — ``pause(run_id) -> dict``
- ``Resumer`` Protocol — ``resume(run_id) -> dict``
- ``StubPauser`` / ``StubResumer`` — default impls that update the
  ``RunLedger``.
"""

from __future__ import annotations

from typing import Any, Protocol

from .run_ledger import RunLedger


class Pauser(Protocol):
    """Protocol for operator pause flow."""

    def pause(self, run_id: str) -> dict[str, Any]: ...


class Resumer(Protocol):
    """Protocol for operator resume flow."""

    def resume(self, run_id: str) -> dict[str, Any]: ...


class _BaseOperator:
    """Shared impl: ledger updates + call history (tuple)."""

    def __init__(self, *, ledger: RunLedger) -> None:
        self._ledger = ledger
        self._calls: list[str] = []

    @property
    def call_history(self) -> tuple[str, ...]:
        return tuple(self._calls)

    def _validate(self, run_id: str) -> None:
        if not run_id:
            raise ValueError("run_id must be non-empty")


class StubPauser(_BaseOperator):
    """Default ``Pauser``: marks RunLedger state = PAUSED."""

    def pause(self, run_id: str) -> dict[str, Any]:
        self._validate(run_id)
        self._calls.append(run_id)
        rec = self._ledger.mark_paused(run_id)
        if rec is None:
            return {"ok": False, "error": "unknown run", "run_id": run_id}
        return {"ok": True, "run_id": run_id, "state": rec.state.value}


class StubResumer(_BaseOperator):
    """Default ``Resumer``: marks RunLedger state = RUNNING."""

    def resume(self, run_id: str) -> dict[str, Any]:
        self._validate(run_id)
        self._calls.append(run_id)
        rec = self._ledger.mark_resume(run_id)
        if rec is None:
            return {"ok": False, "error": "unknown run", "run_id": run_id}
        return {"ok": True, "run_id": run_id, "state": rec.state.value}
