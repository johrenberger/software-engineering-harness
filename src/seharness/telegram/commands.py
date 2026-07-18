"""Telegram command parser for SPEC §'Slice 11: Telegram ingress'.

Provides:
- ``CommandKind`` StrEnum — the 7 supported commands.
- ``ParsedCommand`` frozen Pydantic BaseModel.
- ``MalformedCommandError`` — raised on unparseable input.
- ``CommandParser`` — pure function: text → ParsedCommand.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

_TELEGRAM_MAX = 4096


class CommandKind(StrEnum):
    """The 7 supported Telegram commands (SPEC §'13. Telegram commands')."""

    FEATURE = "/feature"
    STATUS = "/status"
    RUNS = "/runs"
    RESUME = "/resume"
    CANCEL = "/cancel"
    PR = "/pr"
    HELP = "/help"


class ParsedCommand(BaseModel):
    """Frozen representation of one parsed Telegram command.

    Pydantic frozen + extra=forbid per slice 5 style.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CommandKind
    chat_id: int
    args: tuple[str, ...] = ()
    raw_text: str = Field(min_length=1, max_length=_TELEGRAM_MAX)


class MalformedCommandError(Exception):
    """Raised when the parser cannot turn text into a ParsedCommand.

    ``message`` is bounded to ``_TELEGRAM_MAX`` chars to keep error
    paths safe to relay to the user.
    """

    def __init__(self, *, raw: str, reason: str) -> None:
        self.raw = raw
        self.reason = reason
        # Truncate raw to keep the message bounded
        preview = raw[:120] + ("..." if len(raw) > 120 else "")
        self.message = f"malformed command: {reason} (raw={preview!r})"
        super().__init__(self.message)


def _tokenize(text: str) -> list[str]:
    """Split command text into tokens, honoring quoted strings.

    Simple shell-style splitter: words are separated by whitespace;
    single-quoted groups are kept as one token.
    """
    tokens: list[str] = []
    cur: list[str] = []
    in_quote = False
    for ch in text:
        if ch == "'":
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if cur:
                tokens.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


# Required-arg counts: FEATURE requires 2 args when args provided;
# STATUS/RESUME/CANCEL/PR require 1; RUNS/HELP require 0.
_REQUIRED_ARGS: dict[CommandKind, int] = {
    CommandKind.STATUS: 1,
    CommandKind.RESUME: 1,
    CommandKind.CANCEL: 1,
    CommandKind.PR: 1,
    CommandKind.RUNS: 0,
    CommandKind.HELP: 0,
}


class CommandParser:
    """Pure parser: text + chat_id → ParsedCommand.

    No I/O, no side effects. The parser does NOT call the application
    service or transport — only Telegram handlers do that.
    """

    def parse(self, *, chat_id: int, text: str) -> ParsedCommand:
        if not text or not text.strip():
            raise MalformedCommandError(raw=text, reason="empty or whitespace-only text")
        stripped = text.strip()
        if not stripped.startswith("/"):
            raise MalformedCommandError(raw=text, reason="text is not a command (no leading '/')")
        tokens = _tokenize(stripped)
        head = tokens[0]
        try:
            kind = CommandKind(head)
        except ValueError:
            head_preview = head[:120] + ("..." if len(head) > 120 else "")
            raise MalformedCommandError(
                raw=text, reason=f"unknown command: {head_preview}"
            ) from None
        args = tuple(tokens[1:])

        # Argument-count validation
        if kind is CommandKind.FEATURE:
            # 0 args (interactive) or 2 args (inline) are valid; 1 or >2 are malformed
            if len(args) == 1 or len(args) > 2:
                raise MalformedCommandError(
                    raw=text,
                    reason=(
                        "/feature expects 0 (interactive) or 2 "
                        "(<repository-url> <description>) args"
                    ),
                )
        else:
            required = _REQUIRED_ARGS[kind]
            if len(args) != required:
                raise MalformedCommandError(
                    raw=text,
                    reason=f"{kind.value} expects {required} arg(s), got {len(args)}",
                )

        return ParsedCommand(
            kind=kind,
            chat_id=chat_id,
            args=args,
            raw_text=stripped,
        )
