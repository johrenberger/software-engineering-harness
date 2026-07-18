"""Telegram authorization + token redaction for SPEC §'Slice 11'.

Provides:
- ``TelegramAuthorizer`` — allowlist chat IDs.
- ``UnauthorizedChatError`` — raised on unknown chat_id.
- ``Redactor`` — scrubs bot tokens from arbitrary text.
"""

from __future__ import annotations

import re

_BOT_TOKEN_PATTERN = re.compile(
    # Telegram bot tokens look like '<digits>:<base64-ish>'.
    # Format: 6+ digits, colon, 25+ chars of [A-Za-z0-9_-].
    # Accept an optional 'bot' prefix (e.g. /bot<token>) and a
    # trailing '/<path>' suffix for webhook URLs. The token body
    # itself is the digits:base64 part.
    r"(?:\b|/)?\d{6,}:[A-Za-z0-9_\-]{25,}\b"
)


class UnauthorizedChatError(Exception):
    """Raised when a chat_id is not in the allowlist.

    Carries the rejected ``chat_id`` for telemetry.
    """

    chat_id: int

    def __init__(self, *, chat_id: int) -> None:
        self.chat_id = chat_id
        super().__init__(f"unauthorized chat_id: {chat_id}")


class TelegramAuthorizer:
    """Allowlist-based chat authorization.

    Empty ``allowed_chat_ids`` rejects ALL chat IDs (fail-secure
    default). Authorizer is immutable: ``allowed_chat_ids`` is a tuple.
    """

    def __init__(self, *, allowed_chat_ids: tuple[int, ...]) -> None:
        self.allowed_chat_ids: tuple[int, ...] = allowed_chat_ids

    def authorize(self, *, chat_id: int) -> None:
        """Raise ``UnauthorizedChatError`` if chat_id is not allowed."""
        if not isinstance(chat_id, int):
            raise TypeError(f"chat_id must be int, got {type(chat_id).__name__}")
        if chat_id not in self.allowed_chat_ids:
            raise UnauthorizedChatError(chat_id=chat_id)


_REDACTION_TOKEN = "***REDACTED***"


class Redactor:
    """Scrubs Telegram bot tokens from arbitrary text.

    The pattern matches the canonical Bot API token format. Non-token
    text is left untouched.
    """

    def redact(self, text: str) -> str:
        if not text:
            return text
        return _BOT_TOKEN_PATTERN.sub(_REDACTION_TOKEN, text)
