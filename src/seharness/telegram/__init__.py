"""SPEC §'Slice 11: Telegram ingress' subsystem.

Public surface (re-exported):

- Parser: ``CommandKind``, ``ParsedCommand``, ``MalformedCommandError``,
  ``CommandParser``.
- Auth: ``TelegramAuthorizer``, ``UnauthorizedChatError``, ``Redactor``.
- Config: ``TelegramConfig``.
- Service: ``ApplicationService`` Protocol, ``FeatureRequest``.
- Transport: ``TelegramTransport`` Protocol, ``StubTelegramTransport``,
  ``IncomingUpdate``, ``OutgoingMessage``.
- Handlers: ``CommandResult``, ``StubApplicationService``, plus one
  handler per command: ``FeatureHandler``, ``StatusHandler``,
  ``RunsHandler``, ``ResumeHandler``, ``CancelHandler``, ``PrHandler``,
  ``HelpHandler``.

**SPEC §'Do not merge automatically.'** enforcement:
1. Structural: ``ApplicationService`` Protocol declares NO merge methods.
2. Behavioral: ``PrHandler`` scans its outgoing message for forbidden
   tokens (``gh pr merge``/``merge_pull_request``/``auto-merge``/
   ``auto_merge``) and raises if any are present.
3. Test-level: ``test_bounded_command_results.py::test_pr_message_never_contains_merge_commands``
   asserts the message is clean.
"""

from .auth import Redactor, TelegramAuthorizer, UnauthorizedChatError
from .commands import (
    CommandKind,
    CommandParser,
    MalformedCommandError,
    ParsedCommand,
)
from .config import TelegramConfig
from .handlers import (
    CancelHandler,
    CommandResult,
    FeatureHandler,
    HelpHandler,
    PrHandler,
    ResumeHandler,
    RunsHandler,
    StatusHandler,
    StubApplicationService,
)
from .service import ApplicationService, FeatureRequest
from .transport import (
    IncomingUpdate,
    OutgoingMessage,
    StubTelegramTransport,
    TelegramTransport,
)

__all__ = [
    "ApplicationService",
    "CancelHandler",
    "CommandKind",
    "CommandParser",
    "CommandResult",
    "FeatureHandler",
    "FeatureRequest",
    "HelpHandler",
    "IncomingUpdate",
    "MalformedCommandError",
    "OutgoingMessage",
    "ParsedCommand",
    "PrHandler",
    "Redactor",
    "ResumeHandler",
    "RunsHandler",
    "StatusHandler",
    "StubApplicationService",
    "StubTelegramTransport",
    "TelegramAuthorizer",
    "TelegramConfig",
    "TelegramTransport",
    "UnauthorizedChatError",
]
