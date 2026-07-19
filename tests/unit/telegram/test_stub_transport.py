"""G1: tests for StubTelegramTransport coverage.

The StubTelegramTransport is the in-memory transport used by tests
+ non-Telegram transports (e.g. unit-testing the dispatcher).
Without these tests, ``telegram/transport.py`` sits at 72% coverage
because the Stub class is never exercised.

These tests are intentionally lightweight (no fancy fixtures):
the Stub's contract is small (queue + sent list).
"""

from __future__ import annotations

from seharness.telegram import (
    IncomingUpdate,
    OutgoingMessage,
)
from seharness.telegram.transport import StubTelegramTransport


def test_stub_transport_starts_with_empty_queue() -> None:
    """Stub starts empty: no queued updates, no sent messages."""
    transport = StubTelegramTransport()
    assert transport.poll() == ()
    assert transport.sent == []


def test_stub_transport_enqueue_then_poll_returns_tuple() -> None:
    """enqueue() pushes; poll() returns + clears."""
    transport = StubTelegramTransport()
    update1 = IncomingUpdate(chat_id=42, text="hello")
    update2 = IncomingUpdate(chat_id=42, text="world")
    transport.enqueue(update1)
    transport.enqueue(update2)
    polled = transport.poll()
    assert polled == (update1, update2)
    # Queue is cleared after poll.
    assert transport.poll() == ()


def test_stub_transport_send_appends_to_sent_log() -> None:
    """send() records the OutgoingMessage in the .sent list."""
    transport = StubTelegramTransport()
    msg = OutgoingMessage(chat_id=42, text="reply")
    transport.send(msg)
    assert transport.sent == [msg]


def test_stub_transport_send_multiple_appends_in_order() -> None:
    """Multiple sends preserve insertion order (FIFO)."""
    transport = StubTelegramTransport()
    msg1 = OutgoingMessage(chat_id=42, text="first")
    msg2 = OutgoingMessage(chat_id=42, text="second")
    transport.send(msg1)
    transport.send(msg2)
    assert transport.sent == [msg1, msg2]


def test_stub_transport_on_update_property_returns_none() -> None:
    """Stub has no callback hook by default (slice 12 wires the real one)."""
    transport = StubTelegramTransport()
    assert transport.on_update is None


def test_stub_transport_poll_returns_tuple_not_list() -> None:
    """poll() returns a tuple (frozen shape for callers)."""
    transport = StubTelegramTransport()
    transport.enqueue(IncomingUpdate(chat_id=42, text="x"))
    assert isinstance(transport.poll(), tuple)
