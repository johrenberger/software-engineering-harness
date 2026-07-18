"""RED: TelegramBotTransport (real impl) wires python-telegram-bot safely.

SPEC §'21. OpenClaw packaging' Q1=A1 — production transport that
calls python-telegram-bot. Tests use a fake bot to verify the wiring
without actually polling.

The TelegramBotTransport:
- is constructed with a BotToken (str) and an ApplicationService
- exposes send() that translates OutgoingMessage → bot.send_message()
- exposes dispatch() that walks IncomingUpdate → CommandParser → handler
- never starts polling unless .start_polling() is explicitly called
- fails-secure if bot_token is missing/empty (raises ValueError)
- redacts bot_token in __repr__

RED bullets covered:
- TelegramBotTransport rejects empty/missing bot_token.
- send() routes through Python-telegram-bot's send_message.
- dispatch() invokes the right handler per CommandKind.
- __repr__ redacts the bot_token.
- The transport does NOT auto-start polling on construction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from seharness.telegram import (
    CommandKind,
    CommandResult,
    FeatureRequest,
    IncomingUpdate,
    OutgoingMessage,
    ParsedCommand,
    StubApplicationService,
)
from seharness.telegram.transport import TelegramBotTransport


def _make_bot_mock() -> MagicMock:
    bot = MagicMock()
    bot.send_message.return_value = MagicMock(message_id=1)
    return bot


def _make_service() -> StubApplicationService:
    return StubApplicationService()


def test_transport_rejects_empty_bot_token() -> None:
    with pytest.raises(ValueError, match=r"bot_token"):
        TelegramBotTransport(bot_token="", service=_make_service())


def test_transport_rejects_non_string_bot_token() -> None:
    with pytest.raises((ValueError, TypeError)):
        TelegramBotTransport(bot_token=12345, service=_make_service())  # type: ignore[arg-type]


def test_transport_construction_does_not_start_polling() -> None:
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=bot,
    )
    # No implicit calls to start_polling / run_polling.
    assert not bot.start_polling.called
    assert not bot.run_polling.called


def test_transport_send_routes_to_bot_send_message() -> None:
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=bot,
    )
    transport.send(
        OutgoingMessage(
            chat_id=42,
            text="hello",
        )
    )
    bot.send_message.assert_called_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["text"] == "hello"


def test_transport_send_redacts_bot_token_in_response() -> None:
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=bot,
    )
    # OutgoingMessage must not be modified to include the token.
    bot.send_message.return_value = MagicMock(message_id=1)
    transport.send(OutgoingMessage(chat_id=42, text="hello"))
    args = bot.send_message.call_args
    text_arg = args.kwargs.get("text") or args.args[0]
    assert "123456:abcdef" not in text_arg


def test_transport_dispatch_routes_to_feature_handler() -> None:
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(
        chat_id=42,
        text="/feature git@github.com:foo/bar.git add login",
    )
    transport.dispatch(update)
    # StubApplicationService records feature_request call
    assert len(service.feature_calls) == 1


def test_transport_dispatch_routes_to_help_handler() -> None:
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(chat_id=42, text="/help")
    transport.dispatch(update)
    bot.send_message.assert_called()


def test_transport_dispatch_malformed_returns_error_message() -> None:
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(chat_id=42, text="not-a-command")
    transport.dispatch(update)
    bot.send_message.assert_called()
    text = bot.send_message.call_args.kwargs.get("text") or ""
    assert "unknown" in text.lower() or "malformed" in text.lower() or "help" in text.lower()


def test_transport_repr_redacts_bot_token() -> None:
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=_make_bot_mock(),
    )
    rendered = repr(transport)
    assert "123456:abcdef" not in rendered
    assert "***" in rendered or "redact" in rendered.lower()


def test_transport_does_not_start_polling_unless_explicit() -> None:
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=bot,
    )
    # Construction alone should not have started polling.
    assert not bot.start_polling.called


def test_transport_send_bounded_message() -> None:
    """Outgoing messages are truncated at 4096 chars."""
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=_make_service(),
        bot=bot,
    )
    huge = "x" * 5000
    transport.send(OutgoingMessage(chat_id=42, text=huge))
    text = bot.send_message.call_args.kwargs.get("text") or ""
    assert len(text) <= 4096


def test_transport_dispatch_handles_status_command() -> None:
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwx",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(chat_id=42, text="/status run-1")
    transport.dispatch(update)
    bot.send_message.assert_called()
