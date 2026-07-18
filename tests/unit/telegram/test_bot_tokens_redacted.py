"""Tests for SPEC §'Slice 11: Telegram ingress' RED bullet 5.

'bot tokens are redacted':
- Redactor MUST scrub bot tokens from any string it processes.
- Token formats covered: Bot API token (e.g. '123456:ABC-DEF...'),
  webhook URLs with token query strings, raw tokens in messages.
- Redaction is case-insensitive.
- Empty strings / strings without tokens are returned unchanged.
- __repr__ of TelegramConfig redaits token automatically.
"""

from __future__ import annotations

from seharness.telegram.auth import Redactor
from seharness.telegram.config import TelegramConfig


def test_redactor_scrubs_bot_token() -> None:
    """Standard Telegram bot token: '1234567890:ABCDefGHIjklMnOpQRsTUVwxyz'"""
    redactor = Redactor()
    text = "My token is 1234567890:ABCDefGHIjklMnOpQRsTUVwxyz here"
    out = redactor.redact(text)
    assert "1234567890:ABCDefGHIjklMnOpQRsTUVwxyz" not in out
    assert "***REDACTED***" in out


def test_redactor_scrubs_webhook_token() -> None:
    """Webhook URL with ?token=XXX... MUST be scrubbed."""
    redactor = Redactor()
    text = "POST https://api.telegram.org/bot1234567890:ABCDefGHIjklMnOpQRsTUVwxyz/sendMessage"
    out = redactor.redact(text)
    assert "1234567890" not in out
    assert "ABCDefGHI" not in out


def test_redactor_is_case_insensitive() -> None:
    redactor = Redactor()
    text = "Token: 1234567890:abcdefGhIjklMnOpQRsTUVwxyz"
    out = redactor.redact(text)
    assert "abcdefGhIjklMnOpQRsTUVwxyz" not in out


def test_redactor_leaves_clean_text_unchanged() -> None:
    redactor = Redactor()
    text = "this is a normal message"
    out = redactor.redact(text)
    assert out == text


def test_redactor_empty_string_returns_empty() -> None:
    redactor = Redactor()
    assert redactor.redact("") == ""


def test_redactor_scrubs_multiple_tokens() -> None:
    redactor = Redactor()
    text = (
        "first 1234567890:ABCDefGHIjklMnOpQRsTUVwxyz second 9876543210:ZYXWvutsrqpoNMLkjiHGFedcba"
    )
    out = redactor.redact(text)
    assert "1234567890:ABCDefGHIjklMnOpQRsTUVwxyz" not in out
    assert "9876543210:ZYXWvutsrqpoNMLkjiHGFedcba" not in out


def test_redactor_preserves_non_token_colon_groups() -> None:
    """Plain 'key:value' pairs that aren't bot tokens are NOT redacted."""
    redactor = Redactor()
    text = "issue:12345 closed by alice"
    out = redactor.redact(text)
    assert out == text  # bot token format requires 8+ digits, then ':'


def test_telegram_config_repr_redacts_token() -> None:
    config = TelegramConfig(
        bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
        allowed_chat_ids=(12345,),
    )
    text = repr(config)
    assert "1234567890" not in text
    assert "ABCDefGHI" not in text
    assert "***REDACTED***" in text


def test_telegram_config_str_redacts_token() -> None:
    config = TelegramConfig(
        bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
        allowed_chat_ids=(12345,),
    )
    text = str(config)
    assert "1234567890" not in text


def test_telegram_config_get_secret_method() -> None:
    """TelegramConfig provides explicit .bot_token property (not redacted)
    + .redacted_token property for safe display."""
    config = TelegramConfig(
        bot_token="1234567890:ABCDefGHIjklMnOpQRsTUVwxyz",
        allowed_chat_ids=(12345,),
    )
    # Direct access is allowed (process owner)
    assert config.bot_token == "1234567890:ABCDefGHIjklMnOpQRsTUVwxyz"
    # Safe display
    assert "1234567890" not in config.redacted_token


def test_redactor_handles_token_at_message_start() -> None:
    redactor = Redactor()
    text = "1234567890:ABCDefGHIjklMnOpQRsTUVwxyz is my token"
    out = redactor.redact(text)
    assert out.startswith("***REDACTED***")


def test_redactor_handles_token_at_message_end() -> None:
    redactor = Redactor()
    text = "use token 1234567890:ABCDefGHIjklMnOpQRsTUVwxyz"
    out = redactor.redact(text)
    assert out.endswith("***REDACTED***")


def test_redactor_idempotent() -> None:
    """Running redact twice yields the same result."""
    redactor = Redactor()
    text = "token is 1234567890:ABCDefGHIjklMnOpQRsTUVwxyz"
    once = redactor.redact(text)
    twice = redactor.redact(once)
    assert once == twice


def test_redactor_with_no_tokens_returns_unchanged() -> None:
    redactor = Redactor()
    text = "plain text without secrets here"
    assert redactor.redact(text) == text
