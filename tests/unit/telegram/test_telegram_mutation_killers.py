"""Pydantic config mutation killers for SPEC §'Slice 11' Telegram package."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.telegram.auth import TelegramAuthorizer, UnauthorizedChatError
from seharness.telegram.commands import (
    CommandKind,
    MalformedCommandError,
    ParsedCommand,
)
from seharness.telegram.config import TelegramConfig
from seharness.telegram.service import FeatureRequest

# --- CommandKind StrEnum ---


def test_command_kind_is_str_enum() -> None:
    for kind in CommandKind:
        assert isinstance(kind.value, str)


def test_command_kind_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        CommandKind("__mutation_test_unknown__")


def test_command_kind_values_are_stable() -> None:
    """Mutation killer: enum values are stable strings."""
    assert CommandKind.FEATURE.value == "/feature"
    assert CommandKind.STATUS.value == "/status"
    assert CommandKind.RUNS.value == "/runs"
    assert CommandKind.RESUME.value == "/resume"
    assert CommandKind.CANCEL.value == "/cancel"
    assert CommandKind.PR.value == "/pr"
    assert CommandKind.HELP.value == "/help"


# --- ParsedCommand frozen BaseModel ---


def test_parsed_command_is_frozen() -> None:
    cmd = ParsedCommand(kind=CommandKind.RUNS, chat_id=12345, args=(), raw_text="/runs")
    with pytest.raises(ValidationError):
        cmd.kind = CommandKind.HELP  # type: ignore[misc]


def test_parsed_command_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ParsedCommand(  # type: ignore[call-arg]
            kind=CommandKind.RUNS,
            chat_id=12345,
            args=(),
            raw_text="/runs",
            extra_field="mutation",
        )


def test_parsed_command_args_default_to_empty_tuple() -> None:
    cmd = ParsedCommand(kind=CommandKind.RUNS, chat_id=12345, raw_text="/runs")
    assert cmd.args == ()


# --- MalformedCommandError ---


def test_malformed_command_error_is_exception() -> None:
    assert issubclass(MalformedCommandError, Exception)


def test_malformed_command_error_has_bounded_message() -> None:
    err = MalformedCommandError(raw="x" * 10_000, reason="unknown")
    assert len(err.message) <= 4096


def test_malformed_command_error_carries_raw_and_reason() -> None:
    err = MalformedCommandError(raw="/foobar", reason="unknown command")
    assert err.raw == "/foobar"
    assert err.reason == "unknown command"


# --- TelegramAuthorizer ---


def test_authorizer_frozen_allowlist_tuple() -> None:
    auth = TelegramAuthorizer(allowed_chat_ids=(1, 2, 3))
    assert isinstance(auth.allowed_chat_ids, tuple)


def test_unauthorized_chat_error_carries_chat_id() -> None:
    err = UnauthorizedChatError(chat_id=999)
    assert err.chat_id == 999
    assert "999" in str(err)


# --- TelegramConfig ---


def test_telegram_config_is_frozen() -> None:
    config = TelegramConfig(
        bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
        allowed_chat_ids=(12345,),
    )
    with pytest.raises(ValidationError):
        config.bot_token = "x"  # type: ignore[misc]


def test_telegram_config_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TelegramConfig(  # type: ignore[call-arg]
            bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
            allowed_chat_ids=(12345,),
            unknown_field="mutation",
        )


def test_telegram_config_rejects_empty_bot_token() -> None:
    with pytest.raises(ValidationError):
        TelegramConfig(bot_token="", allowed_chat_ids=(12345,))


def test_telegram_config_default_enabled_false() -> None:
    config = TelegramConfig(
        bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
        allowed_chat_ids=(12345,),
    )
    assert config.enabled is False


# --- FeatureRequest ---


def test_feature_request_is_frozen() -> None:
    req = FeatureRequest(repository_url="https://github.com/foo/bar", description="Add X")
    with pytest.raises(ValidationError):
        req.repository_url = "x"  # type: ignore[misc]


def test_feature_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        FeatureRequest(  # type: ignore[call-arg]
            repository_url="https://github.com/foo/bar",
            description="Add X",
            extra_field="mutation",
        )
