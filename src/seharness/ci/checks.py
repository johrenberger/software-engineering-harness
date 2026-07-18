"""GitHub check-run primitives for SPEC §'Slice 10: CI monitoring'.

Provides:
- ``CheckStatus``, ``CheckRunState``, ``CheckConclusion`` enums.
- ``PullRequestCheck``: a single check run on a PR head.
- ``RequiredChecksView``: the snapshot of all checks + the mergeability
  state needed to decide ready-for-review.
- ``ChecksClient`` Protocol + ``StubChecksClient`` (in-memory test impl).

**Structural auto-merge prevention (SPEC §'Do not merge automatically.')**:
The ``ChecksClient`` Protocol has NO ``merge*``, ``auto_merge*``,
``merge_pull_request*`` methods. This is verified by
``tests/unit/ci/test_no_auto_merge.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    """Coarse status of a single check (UI summary)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    QUEUED = "queued"


class CheckRunState(StrEnum):
    """GitHub check-run state."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    COMPLETED = "completed"


class CheckConclusion(StrEnum):
    """GitHub check-run conclusion. None-equivalent means still running."""

    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"
    STALE = "stale"


class PullRequestCheck(BaseModel):
    """A single check on a PR head.

    Frozen + extra=forbid (per SPEC §'Slice 5' Pydantic style).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    state: CheckRunState
    conclusion: CheckConclusion | None = None
    required: bool = False

    @property
    def is_terminal(self) -> bool:
        """A check is terminal iff COMPLETED with a non-None conclusion."""
        return self.state is CheckRunState.COMPLETED and self.conclusion is not None

    @property
    def is_failed(self) -> bool:
        """A check has failed if terminal AND conclusion is FAILURE-class."""
        if not self.is_terminal or self.conclusion is None:
            return False
        return self.conclusion in {
            CheckConclusion.FAILURE,
            CheckConclusion.TIMED_OUT,
            CheckConclusion.CANCELLED,
            CheckConclusion.ACTION_REQUIRED,
        }


class RequiredChecksView(BaseModel):
    """Snapshot of all check-runs on a PR head + mergeability.

    Frozen + extra=forbid.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    branch: str = Field(min_length=1)
    head_sha: str = Field(min_length=1)
    required: tuple[str, ...]
    all_checks: tuple[PullRequestCheck, ...]
    mergeable_unknown: bool


class ChecksClient(Protocol):
    """Protocol for fetching check-run snapshots from GitHub.

    **Structural auto-merge prevention**: this Protocol deliberately
    does NOT declare a ``merge`` / ``merge_pull_request`` method. A
    concrete impl that adds one will be a Protocol structural
    violation at type-check time.
    """

    def fetch_view(self, pr_number: str, branch: str) -> RequiredChecksView: ...


class StubChecksClient:
    """In-memory ChecksClient implementation for tests.

    Holds a ``view_factory`` returning a pre-built
    ``RequiredChecksView`` so each test pins the snapshot it expects.
    """

    def __init__(
        self,
        view_factory: Callable[[], RequiredChecksView] | None = None,
    ) -> None:
        self._view_factory = view_factory
        self._last_pr: str | None = None
        self._last_branch: str | None = None

    def fetch_view(self, pr_number: str, branch: str) -> RequiredChecksView:
        """Return the snapshot from ``view_factory``.

        Raises ``RuntimeError`` if no factory is configured (a test
        using ``StubChecksClient`` must inject a factory).
        """
        self._last_pr = pr_number
        self._last_branch = branch
        if self._view_factory is None:
            raise RuntimeError(
                "StubChecksClient requires a view_factory — configure it in the test."
            )
        view = self._view_factory()
        assert isinstance(view, RequiredChecksView)
        return view

    @property
    def last_called_with(self) -> tuple[str, str]:
        assert self._last_pr is not None and self._last_branch is not None
        return self._last_pr, self._last_branch
