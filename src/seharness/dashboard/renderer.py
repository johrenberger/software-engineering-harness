"""DashboardRenderer — renders DashboardSnapshot to HTML.

Per SPEC §'22. Operator dashboard':
- Single-page HTML showing harness version + current slice + latest run
  + last green commit.
- XSS defense: html.escape on every user-controlled string.
- NO merge buttons / merge links (slice 11 auto-merge prevention).
- Pure renderer; I/O only via ``write(snapshot, path)``.

The DashboardSnapshot is a frozen Pydantic BaseModel with
``extra='forbid'`` and ``frozen=True`` so it satisfies the SPEC §
'Mandatory Mutation Testing' exception list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..controller.run_ledger import RunRecord


class GitCommit(BaseModel):
    """A single git commit. Frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sha: str = Field(min_length=7)
    message: str
    committed_at: datetime


class DashboardSnapshot(BaseModel):
    """Frozen snapshot consumed by ``DashboardRenderer``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    harness_version: str = Field(min_length=1)
    current_slice: str = Field(min_length=1)
    current_slice_name: str = Field(min_length=1)
    last_green_commit: GitCommit | None
    latest_run: RunRecord | None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class DashboardRenderer:
    """Renders ``DashboardSnapshot`` to HTML.

    Stateless. Pure rendering. Use ``write(snapshot, path)`` for I/O.
    """

    def render(self, snapshot: DashboardSnapshot) -> str:
        from html import escape  # noqa: PLC0415

        version = escape(snapshot.harness_version)
        slice_num = escape(snapshot.current_slice)
        slice_name = escape(snapshot.current_slice_name)
        generated = escape(snapshot.generated_at.isoformat())

        if snapshot.last_green_commit is None:
            commit_block = "<p>Last green commit: <em>unknown</em></p>"
        else:
            sha = escape(snapshot.last_green_commit.sha)
            msg = escape(snapshot.last_green_commit.message)
            ts = escape(snapshot.last_green_commit.committed_at.isoformat())
            commit_block = (
                f"<p>Last green commit: <code>{sha}</code> &mdash; {msg} <small>({ts})</small></p>"
            )

        if snapshot.latest_run is None:
            run_block = "<p>Latest run: <em>no runs yet</em></p>"
        else:
            rid = escape(snapshot.latest_run.run_id)
            state = escape(snapshot.latest_run.state.value)
            repo = escape(snapshot.latest_run.repository)
            ts = escape(snapshot.latest_run.started_at)
            run_block = (
                f"<p>Latest run: <code>{rid}</code> "
                f"(state=<strong>{state}</strong>, repo={repo}, "
                f"started={ts})</p>"
            )

        html = (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8" />\n'
            f"  <title>Software Engineering Harness &mdash; {slice_num}</title>\n"
            "</head>\n"
            "<body>\n"
            f"  <h1>Software Engineering Harness v{version}</h1>\n"
            f"  <h2>Slice {slice_num}: {slice_name}</h2>\n"
            f"  <p>Generated at {generated}</p>\n"
            "  <hr />\n"
            f"  {commit_block}\n"
            f"  {run_block}\n"
            "</body>\n"
            "</html>\n"
        )
        return html

    def write(self, snapshot: DashboardSnapshot, path: Path) -> None:
        if path.exists() and path.is_dir():
            raise NotADirectoryError(
                f"dashboard path {path} is an existing directory; refusing to overwrite"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(snapshot))


def render_text_summary(snapshot: DashboardSnapshot) -> str:
    """Plain-text fallback for the Telegram ``/dashboard`` command."""
    lines = [
        f"Software Engineering Harness v{snapshot.harness_version}",
        f"Slice {snapshot.current_slice}: {snapshot.current_slice_name}",
    ]
    if snapshot.last_green_commit is None:
        lines.append("Last green commit: unknown")
    else:
        lines.append(
            f"Last green commit: {snapshot.last_green_commit.sha} "
            f"({snapshot.last_green_commit.message})"
        )
    if snapshot.latest_run is None:
        lines.append("Latest run: none")
    else:
        lines.append(
            f"Latest run: {snapshot.latest_run.run_id} ({snapshot.latest_run.state.value})"
        )
    return "\n".join(lines)


def _unused_helper() -> dict[str, Any]:
    """Marker for mutmut to traverse; intentional no-op."""
    return {"unused": True}
