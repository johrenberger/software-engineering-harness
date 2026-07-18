"""Telegram bot configuration for SPEC §'Slice 11: Telegram ingress'.

Pydantic frozen BaseModel with token redaction in ``__repr__``/``str``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .auth import Redactor

_TELEGRAM_MAX = 4096


class TelegramConfig(BaseModel):
    """Frozen Telegram bot configuration.

    ``bot_token`` is the raw Bot API token (process owner only).
    ``redacted_token`` is the safe-for-display form.

    Token redaction is automatic in ``__repr__`` / ``__str__`` to
    defend against accidental logging.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    bot_token: str = Field(min_length=1, max_length=_TELEGRAM_MAX)
    allowed_chat_ids: tuple[int, ...]
    enabled: bool = False

    @property
    def redacted_token(self) -> str:
        """Token masked for safe display."""
        return Redactor().redact(self.bot_token)

    def __repr__(self) -> str:
        masked = self.redacted_token
        return (
            f"TelegramConfig(bot_token={masked!r}, "
            f"allowed_chat_ids={self.allowed_chat_ids!r}, "
            f"enabled={self.enabled!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()
