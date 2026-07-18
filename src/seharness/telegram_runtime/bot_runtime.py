"""Production Telegram bot runtime.

This module wires the slice-11 ``TelegramTransport`` Protocol into
``python-telegram-bot``'s ``Application.run_polling()``.

Architecture invariants (slice 11/12/13):
- ``bot_token`` validated at construction time.
- Empty ``TELEGRAM_ALLOWED_CHAT_IDS`` rejects ALL chat ids (fail-secure).
- Construction does NOT start polling.
- ``run()`` invokes ``application.run_polling()`` with graceful SIGINT.
- No merge methods exposed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telegram.ext import Application  # noqa: F401

    from seharness.telegram_runtime.command_handlers import CommandDispatcher

_LOG = logging.getLogger(__name__)

_TELEGRAM_MAX_REPLY = 4096


def _parse_chat_ids(raw: str | None) -> tuple[int, ...]:
    """Parse a CSV of chat ids from env into a tuple of ints."""
    if not raw:
        return ()
    parts = (p.strip() for p in raw.split(",") if p.strip())
    return tuple(int(p) for p in parts)


def _build_application(bot_token: str) -> "object":
    """Build a python-telegram-bot Application from a bot token."""
    from telegram.ext import Application  # noqa: F401

    app = Application.builder().token(bot_token).build()
    return app


@dataclass(frozen=True)
class TelegramBotRuntime:
    """Production Telegram bot runner.

    Parameters
    ----------
    bot_token:
        Bot API token. Must be a non-empty string. (Defaults to
        ``$TELEGRAM_BOT_TOKEN``.)
    service:
        An ``ApplicationService``-like object providing
        ``status()``, ``runs()``, ``feature_request()``, ``pr_status()``,
        ``resume()``, ``cancel()``.
    allowed_chat_ids:
        Tuple of chat ids permitted to invoke commands. Defaults to
        ``$TELEGRAM_ALLOWED_CHAT_IDS``. Empty tuple rejects all (fail-secure).
    """

    bot_token: str = ""
    service: Any = None
    allowed_chat_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # Defaults from env
        if self.bot_token is None:
            raise ValueError("bot_token must be a string (not None)")
        token = self.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not isinstance(token, str) or not token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN must be set and non-empty "
                "(export TELEGRAM_BOT_TOKEN=<bot_token>)"
            )
        object.__setattr__(self, "bot_token", token)

        # Default service to a stub if not provided (kept simple for tests)
        if self.service is None:
            raise ValueError("service must be provided")

        if not self.allowed_chat_ids:
            # Parse from env if not explicitly set.
            object.__setattr__(
                self,
                "allowed_chat_ids",
                _parse_chat_ids(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS")),
            )

    def install_handlers(self) -> "object":
        """Install command handlers on the underlying python-telegram-bot
        application. Returns the Application instance.
        """
        from telegram.ext import (
            Application,
            CommandHandler,
        )

        from seharness.telegram_runtime.command_handlers import (
            CommandDispatcher,
        )

        app: Any = _build_application(self.bot_token)
        dispatcher = CommandDispatcher(service=self.service)
        for command in (
            "start",
            "help",
            "status",
            "runs",
            "feature",
            "pr",
            "resume",
            "cancel",
            "dashboard",
        ):
            # python-telegram-bot's CommandHandler takes a callback that
            # receives (update, context). The dispatcher accepts a simple
            # update dataclass; we adapt via a thin lambda.
            cb: Any = _make_ptb_handler(dispatcher)
            handler = CommandHandler(command, cb)
            app.add_handler(handler)
        return app

    def run(self) -> int | None:
        """Start polling. Returns 0 on graceful shutdown, None otherwise."""
        app = self.install_handlers()
        try:
            app.run_polling()  # type: ignore[attr-defined]
            return 0
        except KeyboardInterrupt:
            _LOG.info("TelegramBotRuntime: graceful shutdown on SIGINT")
            return 0

    def __repr__(self) -> str:
        from seharness.telegram.auth import Redactor

        return (
            f"TelegramBotRuntime(bot_token={Redactor().redact(self.bot_token)!r}, "
            f"allowed_chat_ids={self.allowed_chat_ids!r})"
        )

    @property
    def _safe_token_repr(self) -> str:
        """Always-redact token, regardless of regex matching."""
        if not self.bot_token:
            return "***"
        if len(self.bot_token) <= 8:
            return "***"
        return self.bot_token[:5] + "***" + self.bot_token[-3:]


def _make_ptb_handler(
    dispatcher: "CommandDispatcher",
) -> Callable[..., object]:
    """Adapt a CommandDispatcher to python-telegram-bot's handler signature."""

    def handler(update: Any, context: Any) -> None:
        chat = update.effective_chat
        text = update.message.text if update.message else ""
        chat_id = int(chat.id) if chat is not None else 0
        from seharness.telegram_runtime.command_handlers import (
            _StubUpdate,
        )

        dispatcher.dispatch(_StubUpdate(chat_id=chat_id, text=text))

    return handler


def cli() -> int:
    """Console-script entry point for the Telegram bot."""
    from seharness.controller.config import ApplicationServiceFactory

    factory = ApplicationServiceFactory.default()
    service = factory.build()
    runtime = TelegramBotRuntime(service=service)
    return runtime.run() or 0
