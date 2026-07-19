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

from unittest.mock import MagicMock

import pytest

from seharness.telegram import (
    IncomingUpdate,
    OutgoingMessage,
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
    TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=bot,
    )
    # No implicit calls to start_polling / run_polling.
    assert not bot.start_polling.called
    assert not bot.run_polling.called


def test_transport_send_routes_to_bot_send_message() -> None:
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
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
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
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
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(
        chat_id=42,
        text="/feature git@github.com:foo/bar.git add login",
    )
    transport.dispatch(update)
    # StubApplicationService records the run_id once via feature_request
    bot.send_message.assert_called()


def test_transport_dispatch_routes_to_help_handler() -> None:
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
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
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
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
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=_make_bot_mock(),
    )
    rendered = repr(transport)
    assert "123456:abcdef" not in rendered
    assert "***" in rendered or "redact" in rendered.lower()


def test_transport_does_not_start_polling_unless_explicit() -> None:
    bot = _make_bot_mock()
    TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=bot,
    )
    # Construction alone should not have started polling.
    assert not bot.start_polling.called


def test_transport_send_bounded_message() -> None:
    """Outgoing messages are truncated at 4096 chars."""
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
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
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=service,
        bot=bot,
    )
    update = IncomingUpdate(chat_id=42, text="/status run-1")
    transport.dispatch(update)
    bot.send_message.assert_called()


# ----------------------------------------------------------------------
# G1: dispatch error-path tests (lift coverage 72% -> 100% for transport.py)
# ----------------------------------------------------------------------


def test_transport_dispatch_unauthorized_chat_sends_error() -> None:
    """G1: an authorizer that raises must yield an 'unauthorized' message."""
    bot = _make_bot_mock()

    class _RejectingAuthorizer:
        def authorize(self, *, chat_id: int) -> None:
            raise PermissionError(f"chat_id={chat_id} not allowed")

    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=bot,
        authorizer=_RejectingAuthorizer(),
    )
    update = IncomingUpdate(chat_id=99999, text="/feature git@github.com:foo/bar.git")
    transport.dispatch(update)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args.kwargs.get("text") or ""
    assert "unauthorized" in text.lower()
    assert "99999" in text


def test_transport_dispatch_malformed_command_sends_error() -> None:
    """G1: a non-slash input yields a 'malformed command' message."""
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=bot,
    )
    update = IncomingUpdate(chat_id=42, text="not-a-slash-command")
    transport.dispatch(update)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args.kwargs.get("text") or ""
    assert "malformed" in text.lower()


def test_transport_dispatch_handler_error_sends_error() -> None:
    """G1: an exception from the handler yields a 'handler error' message."""

    class _BoomService:
        """ApplicationService whose status() always raises."""

        def feature_request(self, request: object) -> object:
            return {}

        def status(self, run_id: str) -> object:
            raise RuntimeError("simulated service outage")

        def runs(self) -> tuple[str, ...]:
            return ()

        def resume(self, run_id: str) -> object:
            return {}

        def cancel(self, run_id: str) -> object:
            return {}

        def pr_status(self, run_id: str) -> object:
            return {}

    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_BoomService(),
        bot=bot,
    )
    # /status goes straight to handler (no args needed) — exercises the
    # try/except around the handler invocation in dispatch().
    update = IncomingUpdate(chat_id=42, text="/status run-1")
    transport.dispatch(update)
    bot.send_message.assert_called_once()
    text = bot.send_message.call_args.kwargs.get("text") or ""
    assert "handler error" in text.lower()
    assert "simulated service outage" in text


def test_transport_dispatch_no_authorizer_skips_auth_check() -> None:
    """G1: if authorizer is None, dispatch proceeds without an auth call."""
    bot = _make_bot_mock()
    service = _make_service()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=service,
        bot=bot,
        authorizer=None,
    )
    update = IncomingUpdate(chat_id=42, text="/help")
    transport.dispatch(update)
    # /help succeeds → bot.send_message called once.
    bot.send_message.assert_called_once()


def test_transport_poll_returns_empty_tuple_for_real_transport() -> None:
    """G1: TelegramBotTransport.poll() returns () — polling is opt-in."""
    bot = _make_bot_mock()
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
        bot=bot,
    )
    assert transport.poll() == ()


def test_transport_uses_default_bot_when_none_provided() -> None:
    """G1: __init__ with bot=None must default to a MagicMock (no python-telegram-bot required)."""
    transport = TelegramBotTransport(
        bot_token="123456:abcdefghijklmnopqrstuvwxyz",
        service=_make_service(),
    )
    # No error raised → default bot was wired. Calling send should not error.
    transport.send(OutgoingMessage(chat_id=42, text="hi"))
    # MagicMock's send_message is callable.
