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

    update_id: int
    chat_id: int
    text: str = Field(min_length=0, max_length=_TELEGRAM_MAX)


class OutgoingMessage(BaseModel):
    """Frozen outgoing Telegram message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: int
    text: str = Field(min_length=0, max_length=_TELEGRAM_MAX)


class TelegramTransport(Protocol):
    """Protocol for the Telegram transport layer.

    Slice 12 wires the real ``python-telegram-bot`` impl behind this.
    The Telegram command dispatcher depends ONLY on this Protocol.
    """

    def poll(self) -> tuple[IncomingUpdate, ...]: ...

    def send(self, message: OutgoingMessage) -> None: ...


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
