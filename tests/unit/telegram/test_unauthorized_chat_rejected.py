"""Tests for SPEC §'Slice 11: Telegram ingress' RED bullet 1.

'unauthorized chat IDs are rejected':
- TelegramAuthorizer MUST allowlist chat_ids; unknown chat IDs raise
  UnauthorizedChatError.
- Authorized chat IDs return True / raise nothing.
- Empty allowlist rejects ALL chat IDs (defense in depth).
"""

from __future__ import annotations

import pytest

from seharness.telegram.auth import (
    TelegramAuthorizer,
    UnauthorizedChatError,
)


def test_empty_allowlist_rejects_all() -> None:
    """Empty allowlist → all chat IDs are unauthorized (fail-secure default)."""
    auth = TelegramAuthorizer(allowed_chat_ids=())
    with pytest.raises(UnauthorizedChatError):
        auth.authorize(chat_id=12345)


def test_single_authorized_chat_passes() -> None:
    auth = TelegramAuthorizer(allowed_chat_ids=(12345,))
    auth.authorize(chat_id=12345)  # no raise


def test_unauthorized_chat_raises() -> None:
    auth = TelegramAuthorizer(allowed_chat_ids=(12345,))
    with pytest.raises(UnauthorizedChatError) as exc_info:
        auth.authorize(chat_id=99999)
    assert exc_info.value.chat_id == 99999


def test_negative_chat_id_raises() -> None:
    """Negative chat IDs (groups) MUST be validated explicitly."""
    auth = TelegramAuthorizer(allowed_chat_ids=(-100123456,))
    auth.authorize(chat_id=-100123456)  # group allowed
    with pytest.raises(UnauthorizedChatError):
        auth.authorize(chat_id=-100999999)


def test_authorizer_is_callable() -> None:
    """TelegramAuthorizer is callable as a Protocol alternative."""
    auth = TelegramAuthorizer(allowed_chat_ids=(12345,))
    assert callable(auth.authorize)


def test_authorizer_frozen_allowlist() -> None:
    """Mutation killer: allowed_chat_ids must be a tuple (immutable)."""
    auth = TelegramAuthorizer(allowed_chat_ids=(12345, 67890))
    assert isinstance(auth.allowed_chat_ids, tuple)


def test_unauthorized_error_carries_chat_id() -> None:
    """UnauthorizedChatError exposes the rejected chat_id."""
    auth = TelegramAuthorizer(allowed_chat_ids=())
    try:
        auth.authorize(chat_id=42)
    except UnauthorizedChatError as exc:
        assert exc.chat_id == 42
        assert "42" in str(exc)


def test_authorizer_with_multiple_chats() -> None:
    auth = TelegramAuthorizer(allowed_chat_ids=(1, 2, 3, 4, 5))
    for cid in (1, 2, 3, 4, 5):
        auth.authorize(chat_id=cid)  # all allowed
    with pytest.raises(UnauthorizedChatError):
        auth.authorize(chat_id=6)


def test_authorizer_rejects_invalid_types() -> None:
    """Mutation killer: non-int chat_id rejected."""
    auth = TelegramAuthorizer(allowed_chat_ids=(12345,))
    with pytest.raises((TypeError, ValueError)):
        auth.authorize(chat_id="not_an_int")  # type: ignore[arg-type]


def test_unauthorized_error_is_exception() -> None:
    """Mutation killer: UnauthorizedChatError inherits from Exception."""
    assert issubclass(UnauthorizedChatError, Exception)
    err = UnauthorizedChatError(chat_id=999)
    assert err.chat_id == 999