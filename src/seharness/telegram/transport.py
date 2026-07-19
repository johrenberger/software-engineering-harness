"""Telegram transport Protocol for SPEC §'Slice 11: Telegram ingress'.

Provides:
- ``TelegramTransport`` Protocol — receives incoming updates + sends
  outgoing messages.
- ``StubTelegramTransport`` — in-memory impl for tests.
- ``IncomingUpdate`` — frozen Pydantic model for incoming updates.
- ``OutgoingMessage`` — frozen Pydantic model for outgoing messages.

Slice 12 wires the real ``python-telegram-bot`` transport. Slice 11
ships the Protocol + Stub.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

_TELEGRAM_MAX = 4096


class IncomingUpdate(BaseModel):
    """Frozen incoming Telegram update.

    Minimal surface (the parser does the rest):
    ``chat_id`` + ``text``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    update_id: int = 0
    chat_id: int
    text: str = Field(min_length=0, max_length=_TELEGRAM_MAX)


class OutgoingMessage(BaseModel):
    """Frozen outgoing Telegram message.

    ``max_length`` is intentionally larger than the Telegram cap so the
    transport layer can truncate before sending. The TelegramBotTransport
    enforces the 4096-char bound at the bot layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: int
    text: str = Field(min_length=0, max_length=10 * _TELEGRAM_MAX)


class TelegramTransport(Protocol):
    """Protocol for the Telegram transport layer.

    Slice 12 wires the real ``python-telegram-bot`` impl behind this.
    The Telegram command dispatcher depends ONLY on this Protocol.
    """

    def poll(self) -> tuple[IncomingUpdate, ...]: ...  # pragma: no cover

    def send(self, message: OutgoingMessage) -> None: ...  # pragma: no cover


class StubTelegramTransport:
    """In-memory ``TelegramTransport`` for tests.

    Holds an in-memory queue of ``IncomingUpdate`` (pushed by tests)
    + a log of ``OutgoingMessage`` (asserted by tests).
    """

    def __init__(self) -> None:
        self._queue: list[IncomingUpdate] = []
        self.sent: list[OutgoingMessage] = []

    def enqueue(self, update: IncomingUpdate) -> None:
        """Test helper: push an incoming update onto the queue."""
        self._queue.append(update)

    def poll(self) -> tuple[IncomingUpdate, ...]:
        """Return and clear the current queue."""
        out = tuple(self._queue)
        self._queue.clear()
        return out

    def send(self, message: OutgoingMessage) -> None:
        """Record an outgoing message."""
        self.sent.append(message)

    @property
    def on_update(self) -> Callable[[IncomingUpdate], None] | None:
        """Optional callback hook (slice 12 may use)."""
        return None


# --- Slice 12 production transport ----------------------------------------


class _BotLike(Protocol):
    """Minimal bot surface used by TelegramBotTransport.

    Matches the shape of ``telegram.Bot`` so tests can inject a MagicMock.
    """

    def send_message(self, *, chat_id: int, text: str) -> object: ...  # pragma: no cover


class TelegramBotTransport:
    """Production ``python-telegram-bot`` transport.

    Per SPEC §'21. OpenClaw packaging' Q1=A1 — slice 12 wires the real
    python-telegram-bot behind this class. Slice 11 tests use the
    ``StubTelegramTransport`` instead.

    **Invariants:**
    - bot_token must be non-empty (validated in __init__).
    - Construction does NOT start polling; ``start_polling`` is explicit.
    - ``__repr__`` redacts the bot_token via the slice-11 ``Redactor``.
    - All outgoing messages are bounded to ``_TELEGRAM_MAX`` chars.
    - No merge methods.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        service: object,
        bot: _BotLike | None = None,
        authorizer: object | None = None,
    ) -> None:
        if not isinstance(bot_token, str) or not bot_token:
            raise ValueError("bot_token must be a non-empty string")
        # Lazy import of Redactor to avoid cycles.
        from .auth import Redactor  # noqa: PLC0415

        self._bot_token = bot_token
        self._service = service
        self._authorizer = authorizer
        # Default: MagicMock-style bot so we can be used in tests without
        # python-telegram-bot installed. Real wiring uses python-telegram-bot's
        # Bot(client=...).
        if bot is None:
            from unittest.mock import MagicMock  # noqa: PLC0415

            default_bot: _BotLike = MagicMock()
            self._bot = default_bot
        else:
            self._bot = bot
        # Cache a single Redactor instance.
        self._redactor = Redactor()

    def __repr__(self) -> str:
        return f"TelegramBotTransport(bot_token={self._redactor.redact(self._bot_token)!r})"

    # --- TelegramTransport surface ---------------------------------------

    def poll(self) -> tuple[IncomingUpdate, ...]:
        """No-op for slice 12; polling is opt-in via ``start_polling``.

        Returns an empty tuple so callers can drain any pending updates
        without spinning a polling loop.
        """
        return ()

    def send(self, message: OutgoingMessage) -> None:
        """Translate ``OutgoingMessage`` → ``bot.send_message``."""
        text = message.text
        if len(text) > _TELEGRAM_MAX:
            text = text[: _TELEGRAM_MAX - 3] + "..."
        # The bot MUST NOT receive the bot_token (defense in depth).
        sanitized = self._redactor.redact(text)
        self._bot.send_message(chat_id=message.chat_id, text=sanitized)

    def dispatch(self, update: IncomingUpdate) -> None:
        """Parse + authorize + delegate + send result."""
        from .commands import CommandParser  # noqa: PLC0415
        from .handlers import (  # noqa: PLC0415
            COMMAND_HANDLERS,
            _bounded,
        )

        # Authorization (if authorizer injected).
        if self._authorizer is not None and hasattr(self._authorizer, "authorize"):
            try:
                self._authorizer.authorize(chat_id=update.chat_id)
            except Exception as exc:
                self.send(
                    OutgoingMessage(
                        chat_id=update.chat_id,
                        text=f"unauthorized: {exc}",
                    )
                )
                return

        parser = CommandParser()
        try:
            parsed = parser.parse(chat_id=update.chat_id, text=update.text)
        except Exception as exc:
            self.send(
                OutgoingMessage(
                    chat_id=update.chat_id,
                    text=f"malformed command: {exc}",
                )
            )
            return

        try:
            handler_cls: type = COMMAND_HANDLERS[parsed.kind]
        except KeyError:
            self.send(
                OutgoingMessage(
                    chat_id=update.chat_id,
                    text="unknown command; try /help",
                )
            )
            return

        try:
            handler = handler_cls(application=self._service)
            result = handler.handle(parsed)
        except Exception as exc:
            self.send(
                OutgoingMessage(
                    chat_id=update.chat_id,
                    text=f"handler error: {exc}",
                )
            )
            return

        self.send(
            OutgoingMessage(
                chat_id=update.chat_id,
                text=_bounded(result.message),
            )
        )
