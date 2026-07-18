"""Tests for SPEC §'Slice 11: Telegram ingress' RED bullet 4.

'/status, /runs, /resume, /cancel, /pr return bounded results':
- Each handler MUST return a CommandResult with bounded message size
  (Telegram cap = 4096 chars).
- /status returns bounded run summary (state, latest phase, error if any).
- /runs returns bounded list of run IDs (most recent N).
- /resume, /cancel return bounded confirmation/error.
- /pr returns bounded PR status (never includes merge commands — see
  test_no_auto_merge equivalence from slice 10).
"""

from __future__ import annotations

import pytest

from seharness.telegram.commands import CommandKind, ParsedCommand
from seharness.telegram.handlers import (
    CancelHandler,
    CommandResult,
    HelpHandler,
    PrHandler,
    ResumeHandler,
    RunsHandler,
    StatusHandler,
    StubApplicationService,
)

TELEGRAM_MAX = 4096


def _parsed(kind: CommandKind, *args: str, chat_id: int = 12345) -> ParsedCommand:
    return ParsedCommand(kind=kind, chat_id=chat_id, args=args, raw_text=kind.value)


# /status


def test_status_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = StatusHandler(application=app)
    result = handler.handle(_parsed(CommandKind.STATUS, "run-1"))
    assert isinstance(result, CommandResult)
    assert len(result.message) <= TELEGRAM_MAX


def test_status_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = StatusHandler(application=app)
    handler.handle(_parsed(CommandKind.STATUS, "run-1"))
    assert app.status_calls == ("run-1",)


def test_status_handler_returns_error_for_unknown_run() -> None:
    app = StubApplicationService()
    handler = StatusHandler(application=app)
    result = handler.handle(_parsed(CommandKind.STATUS, "unknown"))
    assert result.ok is False


# /runs


def test_runs_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = RunsHandler(application=app)
    result = handler.handle(_parsed(CommandKind.RUNS))
    assert isinstance(result, CommandResult)
    assert len(result.message) <= TELEGRAM_MAX


def test_runs_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = RunsHandler(application=app)
    handler.handle(_parsed(CommandKind.RUNS))
    assert app.runs_calls == 1


def test_runs_handler_returns_bounded_count() -> None:
    """Even with 1000 runs, the message is bounded."""
    app = StubApplicationService(runs=tuple(f"run-{i}" for i in range(1000)))
    handler = RunsHandler(application=app)
    result = handler.handle(_parsed(CommandKind.RUNS))
    assert len(result.message) <= TELEGRAM_MAX


# /resume


def test_resume_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = ResumeHandler(application=app)
    result = handler.handle(_parsed(CommandKind.RESUME, "run-1"))
    assert isinstance(result, CommandResult)
    assert len(result.message) <= TELEGRAM_MAX


def test_resume_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = ResumeHandler(application=app)
    handler.handle(_parsed(CommandKind.RESUME, "run-1"))
    assert app.resume_calls == ("run-1",)


# /cancel


def test_cancel_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = CancelHandler(application=app)
    result = handler.handle(_parsed(CommandKind.CANCEL, "run-1"))
    assert isinstance(result, CommandResult)
    assert len(result.message) <= TELEGRAM_MAX


def test_cancel_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = CancelHandler(application=app)
    handler.handle(_parsed(CommandKind.CANCEL, "run-1"))
    assert app.cancel_calls == ("run-1",)


def test_cancel_handler_returns_confirmation() -> None:
    """Successful cancel returns a bounded confirmation message."""
    app = StubApplicationService()
    handler = CancelHandler(application=app)
    result = handler.handle(_parsed(CommandKind.CANCEL, "run-1"))
    assert result.ok is True


# /pr


def test_pr_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = PrHandler(application=app)
    result = handler.handle(_parsed(CommandKind.PR, "run-1"))
    assert isinstance(result, CommandResult)
    assert len(result.message) <= TELEGRAM_MAX


def test_pr_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = PrHandler(application=app)
    handler.handle(_parsed(CommandKind.PR, "run-1"))
    assert app.pr_calls == ("run-1",)


def test_pr_message_never_contains_merge_commands() -> None:
    """Mutation killer: /pr message MUST NOT include `gh pr merge` or
    `merge_pull_request` (SPEC §'Do not merge automatically.').

    Mirrors slice 10's structural auto-merge prevention.
    """
    app = StubApplicationService()
    handler = PrHandler(application=app)
    result = handler.handle(_parsed(CommandKind.PR, "run-1"))
    lowered = result.message.lower()
    forbidden = ("gh pr merge", "merge_pull_request", "auto-merge", "auto_merge")
    for token in forbidden:
        assert token not in lowered, f"/pr message contains forbidden token: {token}"


def test_pr_handler_returns_error_for_no_pr() -> None:
    app = StubApplicationService()
    handler = PrHandler(application=app)
    result = handler.handle(_parsed(CommandKind.PR, "no-pr-run"))
    assert result.ok is False


# /help


def test_help_handler_lists_all_commands() -> None:
    app = StubApplicationService()
    handler = HelpHandler(application=app)
    result = handler.handle(_parsed(CommandKind.HELP))
    assert result.ok is True
    # Every command name MUST appear in the help text
    for cmd in ("/feature", "/status", "/runs", "/resume", "/cancel", "/pr", "/help"):
        assert cmd in result.message, f"help text missing {cmd}"


def test_help_handler_returns_bounded_result() -> None:
    app = StubApplicationService()
    handler = HelpHandler(application=app)
    result = handler.handle(_parsed(CommandKind.HELP))
    assert len(result.message) <= TELEGRAM_MAX


# CommandResult shape


def test_command_result_is_dataclass() -> None:
    """Mutation killer: CommandResult is a frozen dataclass."""
    result = CommandResult(ok=True, message="x")
    with pytest.raises((AttributeError, TypeError)):
        result.ok = False  # type: ignore[misc]


def test_command_result_rejects_extra_kwargs() -> None:
    """Mutation killer: CommandResult frozen + extra=forbid."""
    with pytest.raises(TypeError):
        CommandResult(ok=True, message="x", bad=1)  # type: ignore[call-arg]
