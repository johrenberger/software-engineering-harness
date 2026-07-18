"""Tests for SPEC §'Slice 11: Telegram ingress' RED bullet 3.

'malformed commands do not start runs':
- CommandParser MUST raise MalformedCommandError on unparseable input.
- Empty text, non-slash text, unknown commands, wrong arg counts all
  raise MalformedCommandError WITHOUT triggering any side effect.
- A misspelled command (e.g. '/feat') is NOT auto-corrected; it's
  rejected as malformed.
"""

from __future__ import annotations

import pytest

from seharness.telegram.commands import (
    CommandKind,
    CommandParser,
    MalformedCommandError,
    ParsedCommand,
)


def test_empty_text_raises() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="")


def test_whitespace_only_raises() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="   \n\t  ")


def test_non_slash_text_raises() -> None:
    """Plain prose is not a command."""
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="hello there")


def test_unknown_command_raises() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/foobar")


def test_misspelled_command_does_not_autocorrect() -> None:
    """'/feat' is not '/feature'; reject explicitly."""
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/feat add login")


def test_help_command_parses() -> None:
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/help")
    assert cmd.kind is CommandKind.HELP


def test_feature_command_with_no_args_parses() -> None:
    """Interactive mode: '/feature' alone is valid (will prompt)."""
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/feature")
    assert cmd.kind is CommandKind.FEATURE
    assert cmd.args == ()


def test_feature_command_with_two_args_parses() -> None:
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/feature https://example.com 'add X'")
    assert cmd.kind is CommandKind.FEATURE
    assert len(cmd.args) == 2


def test_status_requires_run_id() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/status")


def test_status_with_run_id_parses() -> None:
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/status run-abc123")
    assert cmd.kind is CommandKind.STATUS
    assert cmd.args == ("run-abc123",)


def test_runs_command_takes_no_args() -> None:
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/runs")
    assert cmd.kind is CommandKind.RUNS
    assert cmd.args == ()


def test_resume_requires_run_id() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/resume")


def test_cancel_requires_run_id() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/cancel")


def test_pr_requires_run_id() -> None:
    parser = CommandParser()
    with pytest.raises(MalformedCommandError):
        parser.parse(chat_id=12345, text="/pr")


def test_parsed_command_carries_chat_id() -> None:
    """ParsedCommand.chat_id is preserved (for authorization)."""
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/runs")
    assert cmd.chat_id == 12345


def test_parsed_command_carries_raw_text() -> None:
    """ParsedCommand.raw_text preserves the original input (audit/telemetry)."""
    parser = CommandParser()
    cmd = parser.parse(chat_id=12345, text="/runs")
    assert cmd.raw_text == "/runs"


def test_malformed_error_message_bounded() -> None:
    """MalformedCommandError.message MUST be bounded (< 4096 chars)."""
    parser = CommandParser()
    try:
        parser.parse(chat_id=12345, text="/foobar" + "x" * 10_000)
    except MalformedCommandError as exc:
        assert len(exc.message) <= 4096