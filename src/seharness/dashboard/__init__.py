"""Dashboard package — operator dashboard for SPEC §'22. Operator dashboard'.

Provides:
- ``DashboardSnapshot`` — frozen Pydantic model with harness version,
  current slice, last green commit, latest run.
- ``GitCommit`` — frozen dataclass-like model for last-green-commit.
- ``DashboardRenderer`` — pure HTML renderer with html.escape XSS
  defense. No I/O on construction; ``write(snapshot, path)`` for I/O.
"""

from __future__ import annotations

from .renderer import DashboardRenderer, DashboardSnapshot, GitCommit

__all__ = [
    "DashboardRenderer",
    "DashboardSnapshot",
    "GitCommit",
]
