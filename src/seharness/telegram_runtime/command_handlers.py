"""Production command dispatcher.

Maps ``/command args`` text → ApplicationService method → bounded reply.
No merge methods. No workflow logic. Thin shell delegating to the
slice-11/12 ApplicationService protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_TELEGRAM_MAX_REPLY = 4096


@dataclass(frozen=True)
class _StubUpdate:
    """Stand-in for ``telegram.Update`` for non-PTB callers."""

    chat_id: int
    text: str


@dataclass
class CommandDispatcher:
    """Dispatch ``/command args`` text to ApplicationService."""

    service: Any

    def dispatch(self, update: _StubUpdate) -> str:  # noqa: PLR0911
        """Route the update and return the reply text."""
        text = (update.text or "").strip()
        if not text.startswith("/"):
            return self._help_text()
        parts = text[1:].split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if command in ("start", "help"):
                return self._help_text()
            if command == "status":
                return self._bounded(self._status_text())
            if command == "runs":
                return self._bounded(self._runs_text())
            if command == "feature":
                return self._bounded(self._feature_text(arg))
            if command == "pr":
                return self._bounded(self._pr_text(arg))
            if command == "resume":
                return self._bounded(self._resume_text(arg))
            if command == "cancel":
                return self._bounded(self._cancel_text(arg))
            if command == "dashboard":
                return self._bounded(self._dashboard_text())
            return f"unknown command /{command}; try /help"
        except Exception as exc:
            return self._bounded(f"error: {exc}")

    # --- commands --------------------------------------------------------

    def _help_text(self) -> str:
        return (
            "harness commands:\n"
            "/status — current slice + last green\n"
            "/runs — recent run ids\n"
            "/feature <repo> <requirement> — start a feature run\n"
            "/pr <branch> — check PR readiness\n"
            "/resume <run_id> — resume a paused run\n"
            "/cancel <run_id> — cancel a running run\n"
            "/dashboard — text dashboard summary\n"
            "/help — this message"
        )

    def _status_text(self) -> str:
        if hasattr(self.service, "status"):
            data = self.service.status()
            slice_no = data.get("slice", "?") if isinstance(data, dict) else "?"
            last_green = data.get("last_green", "?") if isinstance(data, dict) else "?"
            return f"slice={slice_no} last_green={last_green}"
        return "service unavailable"

    def _runs_text(self) -> str:
        if hasattr(self.service, "runs"):
            runs = self.service.runs()
            if isinstance(runs, (list, tuple)):
                return "runs:\n" + "\n".join(runs)
        return "no runs"

    def _feature_text(self, arg: str) -> str:
        if not arg:
            return "usage: /feature <repo> <requirement>"
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            return "usage: /feature <repo> <requirement>"
        repo, requirement = parts
        if hasattr(self.service, "feature_request"):
            return str(self.service.feature_request(repository=repo, requirement=requirement))
        return "feature_request unavailable"

    def _pr_text(self, arg: str) -> str:
        if not arg:
            return "usage: /pr <branch>"
        if hasattr(self.service, "pr_status"):
            return str(self.service.pr_status(branch=arg))
        return "pr_status unavailable"

    def _resume_text(self, arg: str) -> str:
        if not arg:
            return "usage: /resume <run_id>"
        if hasattr(self.service, "resume"):
            return str(self.service.resume(arg))
        return "resume unavailable"

    def _cancel_text(self, arg: str) -> str:
        if not arg:
            return "usage: /cancel <run_id>"
        if hasattr(self.service, "cancel"):
            return str(self.service.cancel(arg))
        return "cancel unavailable"

    def _dashboard_text(self) -> str:
        if hasattr(self.service, "status"):
            data = self.service.status()
            if isinstance(data, dict):
                return (
                    f"slice={data.get('slice', '?')} "
                    f"state={data.get('state', 'ready')} "
                    f"last_green={data.get('last_green', '?')}"
                )
        return "dashboard unavailable"

    @staticmethod
    def _bounded(text: str) -> str:
        """Bound reply text to Telegram's 4096 char limit."""
        if len(text) <= _TELEGRAM_MAX_REPLY:
            return text
        return text[: _TELEGRAM_MAX_REPLY - 3] + "..."
