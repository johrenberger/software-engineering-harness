"""Telegram command handlers for SPEC §'Slice 11: Telegram ingress'.

Each handler is a thin shell: parse → authorize → delegate to
``ApplicationService``. NO workflow logic lives here.

Handlers return ``CommandResult`` with bounded message size (Telegram
cap = 4096 chars). For ``/pr``, the message MUST NOT contain any
merge-command substrings (SPEC §'Do not merge automatically.' +
slice 10 structural prevention).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from .commands import CommandKind, ParsedCommand
from .service import ApplicationService, FeatureRequest

_TELEGRAM_MAX = 4096
_PR_FORBIDDEN_TOKENS = ("gh pr merge", "merge_pull_request", "auto-merge", "auto_merge")


@dataclass(frozen=True)
class CommandResult:
    """Frozen result of one command execution.

    ``message`` is bounded to ``_TELEGRAM_MAX`` chars (Telegram cap).
    """

    ok: bool
    message: str


# ApplicationService result shapes (loose; slice 12 fills in real types)


class _TelegramHandler(Protocol):
    """Common Protocol shape for all handlers."""

    def handle(self, cmd: ParsedCommand) -> CommandResult: ...


class StubApplicationService:
    """In-memory ``ApplicationService`` for tests.

    Records every call so tests can assert on the call sequence
    (mirrors slice 9's StubPullRequestClient pattern).
    """

    def __init__(
        self,
        *,
        runs: tuple[str, ...] = ("run-1", "run-2", "run-3"),
    ) -> None:
        self.calls: tuple[FeatureRequest, ...] = ()
        self.status_calls: tuple[str, ...] = ()
        self.runs_calls: int = 0
        self.resume_calls: tuple[str, ...] = ()
        self.cancel_calls: tuple[str, ...] = ()
        self.pr_calls: tuple[str, ...] = ()
        self._runs = runs

    def feature_request(self, request: FeatureRequest) -> object:
        self.calls = (*self.calls, request)
        return {"run_id": f"run-{len(self.calls)}"}

    def status(self, run_id: str) -> object:
        self.status_calls = (*self.status_calls, run_id)
        if run_id not in self._runs:
            return {"ok": False, "reason": "unknown run"}
        return {
            "ok": True,
            "state": "running",
            "phase": "implementation",
            "run_id": run_id,
        }

    def runs(self) -> tuple[str, ...]:
        self.runs_calls += 1
        return self._runs

    def resume(self, run_id: str) -> object:
        self.resume_calls = (*self.resume_calls, run_id)
        return {"ok": True, "run_id": run_id}

    def cancel(self, run_id: str) -> object:
        self.cancel_calls = (*self.cancel_calls, run_id)
        return {"ok": True, "run_id": run_id}

    def pr_status(self, run_id: str) -> object:
        self.pr_calls = (*self.pr_calls, run_id)
        if run_id not in self._runs:
            return {"ok": False, "reason": "no PR for run"}
        # Slice 10's PollResult shape (mirrored)
        return {
            "ok": True,
            "run_id": run_id,
            "outcome": "ready",
            "url": f"https://github.com/example/pull/{run_id}",
        }


def _bounded(text: str, limit: int = _TELEGRAM_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _as_dict(result: Any) -> dict[str, Any]:
    """Cast an ApplicationService result (Protocol returns ``object``)
    to a ``dict`` for indexing. Slice 12 wires real Pydantic models."""
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    return cast(dict[str, Any], dict(result))


class FeatureHandler:
    """Handler for ``/feature``.

    0 args → interactive prompt (error message guiding user).
    2 args → invoke ``application.feature_request(...)``.
    """

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        if len(cmd.args) == 0:
            return CommandResult(
                ok=False,
                message=(
                    "/feature expects a repository URL and description. "
                    "Example: /feature https://github.com/foo/bar 'Add login'"
                ),
            )
        repo, *desc_parts = cmd.args
        description = " ".join(desc_parts)
        request = FeatureRequest(repository_url=repo, description=description)
        result_raw = self.application.feature_request(request)
        result = _as_dict(result_raw)
        return CommandResult(
            ok=True,
            message=_bounded(f"started run for {repo}: {description} (run_id={result['run_id']})"),
        )


class StatusHandler:
    """Handler for ``/status <run-id>``."""

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        run_id = cmd.args[0]
        result_raw = self.application.status(run_id)
        result = _as_dict(result_raw)
        if not result.get("ok"):
            return CommandResult(
                ok=False,
                message=_bounded(f"unknown run: {run_id}"),
            )
        return CommandResult(
            ok=True,
            message=_bounded(f"run {run_id}: state={result['state']}, phase={result['phase']}"),
        )


class RunsHandler:
    """Handler for ``/runs`` (no args)."""

    MAX_RUNS = 50  # bounded display

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        runs = self.application.runs()
        shown = runs[: self.MAX_RUNS]
        lines = [f"{i + 1}. {r}" for i, r in enumerate(shown)]
        if len(runs) > self.MAX_RUNS:
            lines.append(f"... ({len(runs) - self.MAX_RUNS} more)")
        return CommandResult(
            ok=True,
            message=_bounded("active runs:\n" + "\n".join(lines) if lines else "no active runs"),
        )


class ResumeHandler:
    """Handler for ``/resume <run-id>``."""

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        run_id = cmd.args[0]
        result_raw = self.application.resume(run_id)
        result = _as_dict(result_raw)
        return CommandResult(
            ok=bool(result.get("ok")),
            message=_bounded(f"resumed {run_id}"),
        )


class CancelHandler:
    """Handler for ``/cancel <run-id>``."""

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        run_id = cmd.args[0]
        result_raw = self.application.cancel(run_id)
        result = _as_dict(result_raw)
        return CommandResult(
            ok=bool(result.get("ok")),
            message=_bounded(f"cancelled {run_id}"),
        )


class PrHandler:
    """Handler for ``/pr <run-id>``.

    SPEC §'Do not merge automatically.' — the response MUST NOT
    contain any merge-command substrings.
    """

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        run_id = cmd.args[0]
        result_raw = self.application.pr_status(run_id)
        result = _as_dict(result_raw)
        if not result.get("ok"):
            return CommandResult(
                ok=False,
                message=_bounded(f"no PR for run {run_id}"),
            )
        url = result.get("url", "")
        outcome = result.get("outcome", "unknown")
        message = _bounded(f"PR for {run_id}: outcome={outcome} url={url}")
        # Defense in depth: scan for forbidden tokens
        lowered = message.lower()
        for token in _PR_FORBIDDEN_TOKENS:
            assert token not in lowered, f"/pr message contains forbidden token: {token}"
        return CommandResult(ok=True, message=message)


class HelpHandler:
    """Handler for ``/help``."""

    def __init__(self, *, application: ApplicationService) -> None:
        self.application = application

    def handle(self, cmd: ParsedCommand) -> CommandResult:
        return CommandResult(
            ok=True,
            message=_bounded(
                "Available commands:\n"
                "/feature <repo-url> <description> — start a run\n"
                "/status <run-id> — show run status\n"
                "/runs — list active runs\n"
                "/resume <run-id> — resume a paused run\n"
                "/cancel <run-id> — cancel a run\n"
                "/pr <run-id> — show PR status\n"
                "/help — show this message"
            ),
        )


COMMAND_HANDLERS = {
    CommandKind.FEATURE: FeatureHandler,
    CommandKind.STATUS: StatusHandler,
    CommandKind.RUNS: RunsHandler,
    CommandKind.RESUME: ResumeHandler,
    CommandKind.CANCEL: CancelHandler,
    CommandKind.PR: PrHandler,
    CommandKind.HELP: HelpHandler,
}
