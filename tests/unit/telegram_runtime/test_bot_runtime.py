"""RED tests for the production python-telegram-bot runtime wiring.

The slice-11 TelegramBotTransport only builds the bot handle + dispatch
loop. Slice 13 wires it into python-telegram-bot's Application.run_polling()
with graceful shutdown, env-driven config, and a real Update → ApplicationService
path.

These tests pin the runtime contract — the implementation must satisfy
the slice-13 SPEC §23 Part A bullets 1-2.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest


class _StubApplicationService:
    """Stand-in for the slice-11 ApplicationService protocol."""


def _import_runtime() -> object:
    from seharness.telegram_runtime.bot_runtime import TelegramBotRuntime

    return TelegramBotRuntime


def test_runtime_constructor_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cls = _import_runtime()
    with pytest.raises((RuntimeError, ValueError, OSError)):
        cls(service=_StubApplicationService())


def test_runtime_constructor_requires_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with pytest.raises((TypeError, ValueError, AttributeError)):
        cls()  # type: ignore[call-arg]


def test_runtime_reads_allowed_chat_ids_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111,222,333")
    cls = _import_runtime()
    runtime = cls(service=_StubApplicationService())
    # Allowed chat ids parsed as a tuple of ints.
    assert runtime.allowed_chat_ids == (111, 222, 333)


def test_runtime_defaults_allowed_chat_ids_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    cls = _import_runtime()
    runtime = cls(service=_StubApplicationService())
    # Default: empty tuple → fail-secure (slice 11 invariant).
    assert runtime.allowed_chat_ids == ()


def test_runtime_does_not_start_polling_on_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with patch(
        "seharness.telegram_runtime.bot_runtime._build_application"
    ) as build:
        app = MagicMock()
        build.return_value = app
        runtime = cls(service=_StubApplicationService())
    # Construction must NOT have started polling.
    assert not app.run_polling.called


def test_runtime_run_starts_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with patch(
        "seharness.telegram_runtime.bot_runtime._build_application"
    ) as build:
        app = MagicMock()
        build.return_value = app
        runtime = cls(service=_StubApplicationService())
        runtime.run()
    assert app.run_polling.called


def test_runtime_run_handles_sigint(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGINT during polling must trigger graceful shutdown, not crash."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with patch(
        "seharness.telegram_runtime.bot_runtime._build_application"
    ) as build:
        app = MagicMock()
        # Simulate KeyboardInterrupt raised by run_polling on SIGINT.
        app.run_polling.side_effect = KeyboardInterrupt()
        build.return_value = app
        runtime = cls(service=_StubApplicationService())
        # Must return cleanly, not propagate the exception.
        result = runtime.run()
    assert result is None or result == 0


def test_runtime_installs_command_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime must register exactly one handler per slice-11 command."""
    from telegram.ext import Application  # type: ignore[import-untyped]

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with patch.object(Application, "add_handler") as add_handler:
        runtime = cls(service=_StubApplicationService())
        runtime.install_handlers()
    # At least 8 commands: /start /help /status /runs /resume /cancel /pr /feature /dashboard
    assert add_handler.call_count >= 8


def test_runtime_token_redacted_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    runtime = cls(service=_StubApplicationService())
    rendered = repr(runtime)
    assert "123456:abcdef" not in rendered


def test_runtime_does_not_start_polling_when_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:abcdefghijklmnopqrstuvwx")
    cls = _import_runtime()
    with patch(
        "seharness.telegram_runtime.bot_runtime._build_application"
    ) as build:
        app = MagicMock()
        build.return_value = app
        runtime = cls(service=_StubApplicationService())
    # After construction alone, the app must not have started polling.
    assert not app.run_polling.called
    # Even calling install_handlers does not start polling.
    runtime.install_handlers()
    assert not app.run_polling.called
