"""RED tests for slice-13 production command handlers.

Each /command dispatches to the ApplicationService. The handler layer
wraps each call in a try/except + bounded message formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest


@dataclass
class _StubUpdate:
    """Stand-in for telegram.Update carrying a message + effective_chat."""

    chat_id: int
    text: str


def _import_handlers() -> object:
    from seharness.telegram_runtime.command_handlers import CommandDispatcher

    return CommandDispatcher


def _make_service() -> MagicMock:
    svc = MagicMock()
    svc.status.return_value = {"ok": True, "slice": "12", "last_green": "9cd4831"}
    svc.runs.return_value = ("run-001", "run-002")
    svc.feature_request.return_value = "feature accepted as run-001"
    svc.pr_status.return_value = "ready"
    return svc


def test_dispatcher_dispatches_start_to_help_message() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/start")
    reply = dispatcher.dispatch(update)
    assert "help" in reply.lower() or "harness" in reply.lower()


def test_dispatcher_dispatches_help_to_help_message() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/help")
    reply = dispatcher.dispatch(update)
    assert "/feature" in reply or "commands" in reply.lower()


def test_dispatcher_dispatches_status() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/status")
    reply = dispatcher.dispatch(update)
    assert "12" in reply or "slice" in reply.lower()


def test_dispatcher_dispatches_runs() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/runs")
    reply = dispatcher.dispatch(update)
    assert "run-001" in reply or "run-002" in reply


def test_dispatcher_dispatches_feature_with_repo_and_requirement() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(
        chat_id=42,
        text="/feature git@github.com:foo/bar.git add login screen",
    )
    reply = dispatcher.dispatch(update)
    svc.feature_request.assert_called_once()
    assert "run-001" in reply or "accepted" in reply.lower()


def test_dispatcher_dispatches_pr() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/pr agent/12-openclaw-packaging")
    reply = dispatcher.dispatch(update)
    svc.pr_status.assert_called_once()
    assert "ready" in reply.lower() or "still" in reply.lower()


def test_dispatcher_dispatches_resume_with_run_id() -> None:
    cls = _import_handlers()
    svc = MagicMock()
    svc.resume.return_value = "resumed run-001"
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/resume run-001")
    reply = dispatcher.dispatch(update)
    svc.resume.assert_called_once_with("run-001")


def test_dispatcher_dispatches_cancel_with_run_id() -> None:
    cls = _import_handlers()
    svc = MagicMock()
    svc.cancel.return_value = "cancelled run-001"
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/cancel run-001")
    reply = dispatcher.dispatch(update)
    svc.cancel.assert_called_once_with("run-001")


def test_dispatcher_returns_error_for_unknown_command() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/unknown")
    reply = dispatcher.dispatch(update)
    assert "unknown" in reply.lower() or "help" in reply.lower()


def test_dispatcher_bounds_reply_to_4096_chars() -> None:
    cls = _import_handlers()
    svc = _make_service()
    svc.status.return_value = {"ok": True, "huge": "x" * 10000}
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/status")
    reply = dispatcher.dispatch(update)
    assert len(reply) <= 4096


def test_dispatcher_handles_service_exception_gracefully() -> None:
    cls = _import_handlers()
    svc = MagicMock()
    svc.status.side_effect = RuntimeError("boom")
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/status")
    reply = dispatcher.dispatch(update)
    assert "error" in reply.lower() or "boom" in reply.lower()


def test_dispatcher_dashboard_returns_text_summary() -> None:
    cls = _import_handlers()
    svc = _make_service()
    dispatcher = cls(service=svc)
    update = _StubUpdate(chat_id=42, text="/dashboard")
    reply = dispatcher.dispatch(update)
    # Text fallback per SPEC §22.
    assert isinstance(reply, str)
    assert len(reply) > 0
    assert len(reply) <= 4096
