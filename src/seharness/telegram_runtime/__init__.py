"""Production telegram runtime wiring.

This package exposes the ``TelegramBotRuntime`` (production bot runner)
and the ``CommandDispatcher`` (per-command router). The slice-11
``TelegramTransport`` Protocol is implemented by this runtime in
production deployments.
"""

from seharness.telegram_runtime.bot_runtime import TelegramBotRuntime
from seharness.telegram_runtime.command_handlers import CommandDispatcher

__all__ = ["CommandDispatcher", "TelegramBotRuntime"]
