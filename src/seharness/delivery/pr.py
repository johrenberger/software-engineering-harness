"""PullRequestClient Protocol + stub. Per SPEC §'19. Git Automation'.

Models NEVER call this directly. The controller wires the production
GitHub client in slice 9's wiring layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PullRequestClient(Protocol):
    """Pull request operations. Production impl lands in slice 10 (CI monitoring)."""

    def create(
        self,
        *,
        branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        """Create a PR; returns the URL."""
        ...

    def get(self, pr_url: str) -> dict[str, object]:
        """Fetch PR metadata."""
        ...


class StubPullRequestClient:
    """In-memory PR client for tests. NOT for production use."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self._counter = 0

    def create(
        self,
        *,
        branch: str,
        title: str,
        body: str,
        draft: bool = True,
    ) -> str:
        self._counter += 1
        url = f"https://github.com/test/test/pull/{self._counter}"
        self.created.append(
            {"branch": branch, "title": title, "body": body, "draft": draft, "url": url}
        )
        return url

    def get(self, pr_url: str) -> dict[str, object]:
        for record in self.created:
            if record["url"] == pr_url:
                return record
        return {}
