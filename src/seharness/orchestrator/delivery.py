"""WP6 (story K) — provider-neutral delivery service Protocols.

The orchestrator's draft-PR + CI-readiness phases previously called
in-line ``PullRequestClient.create`` and a stub ``ci_monitor.run``.
WP6 wires these through Protocol-typed injection points so the
orchestrator stays decoupled from the concrete git / GitHub / CI
implementations.

The Protocols follow the same pattern as WP3 service composition
(:mod:`seharness.orchestrator.services`):

* ``DeliveryService`` — branches, stages, commits, pushes, opens PR.
* ``CiReadinessService`` — checks that every required CI status ran
  against the exact recorded PR head SHA.

Two compositions ship:

* ``DeterministicDeliveryComposition`` — ``StubPullRequestClient`` +
  in-memory idempotency + a no-network CI readiness check. Default
  so existing tests + the orchestrator's vertical slice keep passing.
* ``SubprocessDeliveryComposition`` — ``SubprocessGitBackend`` for
  real git operations + ``IdempotencyStore`` (file-based) for
  replay safety. Production deployments swap this in via
  ``Orchestrator(delivery=...)``.

Idempotency is enforced at two levels:

1. The ``IdempotencyStore`` records ``(run_id, task_id, commit_sha,
   branch, pr_url)``. Replays return the cached record.
2. The ``DeliveryService`` consults the store BEFORE creating a new
   branch / commit / PR, so duplicate work is impossible by
   construction.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from seharness.delivery.backend import GitBackend, SubprocessGitBackend
from seharness.delivery.branch import BranchFormat, BranchService
from seharness.delivery.commit import (
    AuthorizedFileSet,
    CommitMessage,
    CommitService,
)
from seharness.delivery.idempotency import (
    IdempotencyKey,
    IdempotencyRecord,
    IdempotencyStore,
)
from seharness.delivery.pr import PullRequestClient, StubPullRequestClient

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryOutcome:
    """Outcome of a single delivery invocation.

    ``branch`` / ``commit_sha`` / ``pr_url`` are populated on success.
    ``replayed`` is True when the service returned a cached record
    from a prior invocation (no new branch / commit / PR was
    created).
    """

    branch: str
    commit_sha: str
    pr_url: str | None
    replayed: bool = False


class CiStatus(BaseModel):
    """A single CI check result reported by the readiness service.

    The status is a closed Literal so the orchestrator can branch
    without fear of unknown values. ``head_sha`` is the SHA the
    check ran against — the readiness service rejects statuses
    whose ``head_sha`` does not match the recorded PR head.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    name: str = Field(min_length=1)
    status: str = Field(
        pattern=r"^(success|failure|pending|cancelled|skipped|neutral|stale|missing)$"
    )
    head_sha: str = Field(min_length=1)


@dataclass(frozen=True)
class CiReadinessOutcome:
    """Result of the CI readiness gate.

    WP6 acceptance criteria:

    * ``ready`` requires a draft PR and successful required checks
      for the exact head SHA.
    * ``stale`` indicates a check ran against an older SHA and must
      not mark the run ready.
    * ``blocked`` indicates a missing CI configuration in production.
    """

    ready: bool
    state: str  # one of "ready", "stale", "blocked", "paused", "failed"
    statuses: tuple[CiStatus, ...]
    required_checks: tuple[str, ...]
    recorded_head_sha: str | None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class DeliveryService(Protocol):
    """Branches, stages, commits, pushes, opens a draft PR.

    Implementations MUST be idempotent: replaying with the same
    ``IdempotencyKey`` returns the cached record rather than
    creating a duplicate branch / commit / PR.
    """

    def deliver(
        self,
        *,
        repo_root: Path,
        run_id: str,
        task_id: str,
        title: str,
        body: str,
        authorized_files: Sequence[str],
        commit_message: CommitMessage,
        idempotency_root: Path,
    ) -> DeliveryOutcome: ...


@runtime_checkable
class CiReadinessService(Protocol):
    """Decide whether the CI gate passes for the recorded PR head SHA.

    The service receives the recorded head SHA and the list of
    required checks; it returns a ``CiReadinessOutcome`` whose
    ``ready`` bool is the orchestrator's only signal. Per WP6:

    * A stale CI result for an earlier SHA MUST NOT mark the run
      ready (``state='stale'``, ``ready=False``).
    * Missing CI configuration in production profile MUST produce
      ``state='blocked'`` / ``state='paused'``.
    """

    def check(
        self,
        *,
        recorded_head_sha: str,
        statuses: Sequence[CiStatus],
        required_checks: Sequence[str],
    ) -> CiReadinessOutcome: ...


@runtime_checkable
class DeliveryComposition(Protocol):
    """Aggregator — the orchestrator consumes both services."""

    delivery: DeliveryService
    readiness: CiReadinessService

    @property
    def kind(self) -> str:
        """String discriminator. ``"deterministic"`` for the offline
        default, ``"subprocess"`` for the real-git production
        composition. The orchestrator uses this to decide whether
        ``validate_runtime_profile_adapters`` is required."""
        ...


# ---------------------------------------------------------------------------
# Deterministic composition (default)
# ---------------------------------------------------------------------------


class DeterministicDeliveryService:
    """Idempotent delivery backed by the in-memory stub PR client.

    Records each invocation in ``IdempotencyStore`` so replays
    return the cached branch / commit / PR rather than creating
    duplicates. This is the historical behaviour, exposed as a
    Protocol-typed injection point.
    """

    def __init__(
        self,
        *,
        pr_client: PullRequestClient | None = None,
    ) -> None:
        self._pr_client = pr_client or StubPullRequestClient()

    @property
    def pr_client(self) -> PullRequestClient:
        return self._pr_client

    def deliver(
        self,
        *,
        repo_root: Path,
        run_id: str,
        task_id: str,
        title: str,
        body: str,
        authorized_files: Sequence[str],
        commit_message: CommitMessage,
        idempotency_root: Path,
    ) -> DeliveryOutcome:
        store = IdempotencyStore(idempotency_root)
        key = IdempotencyKey(run_id=run_id, task_id=task_id)
        cached = store.get(key)
        if cached is not None:
            return DeliveryOutcome(
                branch=cached.branch,
                commit_sha=cached.commit_sha,
                pr_url=cached.pr_url,
                replayed=True,
            )
        branch = f"agent/feature-{run_id.replace('orch-', '')}-{task_id}"
        # Stub commit SHA: a synthetic value so the orchestrator can
        # surface a head SHA in ``RunContext`` without touching git.
        commit_sha = f"stub-{run_id}-{task_id}"
        pr_url = self._pr_client.create(
            branch=branch,
            title=title,
            body=body,
            draft=True,
        )
        store.put(
            key,
            IdempotencyRecord(
                commit_sha=commit_sha,
                branch=branch,
                pr_url=pr_url,
            ),
        )
        return DeliveryOutcome(
            branch=branch,
            commit_sha=commit_sha,
            pr_url=pr_url,
            replayed=False,
        )


class DeterministicCiReadinessService:
    """Head-SHA matching + required-checks evaluation.

    Returns ``ready=True`` only when:
    1. The recorded head SHA is non-empty.
    2. Every required check has a status whose ``head_sha`` matches.
    3. Every required check has ``status='success'``.
    """

    def check(
        self,
        *,
        recorded_head_sha: str,
        statuses: Sequence[CiStatus],
        required_checks: Sequence[str],
    ) -> CiReadinessOutcome:
        if not recorded_head_sha:
            return CiReadinessOutcome(
                ready=False,
                state="blocked",
                statuses=tuple(statuses),
                required_checks=tuple(required_checks),
                recorded_head_sha=None,
            )
        by_name: dict[str, CiStatus] = {s.name: s for s in statuses}
        for required in required_checks:
            status = by_name.get(required)
            if status is None:
                return CiReadinessOutcome(
                    ready=False,
                    state="blocked",
                    statuses=tuple(statuses),
                    required_checks=tuple(required_checks),
                    recorded_head_sha=recorded_head_sha,
                )
            if status.head_sha != recorded_head_sha:
                return CiReadinessOutcome(
                    ready=False,
                    state="stale",
                    statuses=tuple(statuses),
                    required_checks=tuple(required_checks),
                    recorded_head_sha=recorded_head_sha,
                )
            if status.status != "success":
                return CiReadinessOutcome(
                    ready=False,
                    state="pending",
                    statuses=tuple(statuses),
                    required_checks=tuple(required_checks),
                    recorded_head_sha=recorded_head_sha,
                )
        return CiReadinessOutcome(
            ready=True,
            state="ready",
            statuses=tuple(statuses),
            required_checks=tuple(required_checks),
            recorded_head_sha=recorded_head_sha,
        )


class DeterministicDeliveryComposition:
    """Default composition: stub PR client + head-SHA CI matching."""

    kind = "deterministic"

    def __init__(
        self,
        *,
        pr_client: PullRequestClient | None = None,
    ) -> None:
        self.delivery: DeliveryService = DeterministicDeliveryService(pr_client=pr_client)
        self.readiness: CiReadinessService = DeterministicCiReadinessService()


# ---------------------------------------------------------------------------
# Subprocess composition (production)
# ---------------------------------------------------------------------------


class SubprocessDeliveryService:
    """Real git operations + idempotency.

    Wired through the existing ``GitBackend`` /
    ``BranchService`` / ``CommitService`` so the orchestrator can
    ship real commits in production. Idempotency is enforced via
    ``IdempotencyStore``: replays return the cached record.
    """

    def __init__(
        self,
        *,
        backend: GitBackend | None = None,
        branch_format: BranchFormat | None = None,
        pr_client: PullRequestClient | None = None,
    ) -> None:
        self._backend = backend or SubprocessGitBackend()
        self._branches = BranchService(backend=self._backend, branch_format=branch_format)
        self._pr_client = pr_client or StubPullRequestClient()

    def deliver(
        self,
        *,
        repo_root: Path,
        run_id: str,
        task_id: str,
        title: str,
        body: str,
        authorized_files: Sequence[str],
        commit_message: CommitMessage,
        idempotency_root: Path,
    ) -> DeliveryOutcome:
        store = IdempotencyStore(idempotency_root)
        key = IdempotencyKey(run_id=run_id, task_id=task_id)
        cached = store.get(key)
        if cached is not None:
            return DeliveryOutcome(
                branch=cached.branch,
                commit_sha=cached.commit_sha,
                pr_url=cached.pr_url,
                replayed=True,
            )
        branch_name = self._branches.create(
            repo_root,
            feature_id=run_id.replace("orch-", ""),
            slug=task_id,
        )
        authorized = AuthorizedFileSet(allowed_paths=tuple(authorized_files), prohibited_paths=())
        commits = CommitService(backend=self._backend)
        commits.stage(
            repo_root=repo_root,
            files=tuple(authorized_files),
            authorized=authorized,
        )
        commit_sha = commits.commit(
            repo_root=repo_root,
            message=commit_message,
            author_name="seharness",
            author_email="seharness@local",
        )
        pr_url = self._pr_client.create(branch=branch_name, title=title, body=body, draft=True)
        store.put(
            key,
            IdempotencyRecord(
                commit_sha=commit_sha,
                branch=branch_name,
                pr_url=pr_url,
            ),
        )
        return DeliveryOutcome(
            branch=branch_name,
            commit_sha=commit_sha,
            pr_url=pr_url,
            replayed=False,
        )


class SubprocessDeliveryComposition:
    """Production composition: real git + head-SHA CI matching."""

    kind = "subprocess"

    def __init__(
        self,
        *,
        backend: GitBackend | None = None,
        branch_format: BranchFormat | None = None,
        pr_client: PullRequestClient | None = None,
    ) -> None:
        self.delivery: DeliveryService = SubprocessDeliveryService(
            backend=backend,
            branch_format=branch_format,
            pr_client=pr_client,
        )
        self.readiness: CiReadinessService = DeterministicCiReadinessService()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def build_required_checks(
    plan_required_checks: Sequence[str] | None,
) -> tuple[str, ...]:
    """Derive the canonical required-check list for a run.

    WP6 acceptance: ``The ready state requires a draft PR and
    successful required checks for the exact head SHA.`` The list
    is sourced from the plan (the deterministic ``_PlanBuilder``
    does not populate it yet, so the default is the harness's
    quality-gate check name).
    """
    if plan_required_checks:
        return tuple(plan_required_checks)
    return ("quality-gate",)


__all__ = [
    "CiReadinessOutcome",
    "CiReadinessService",
    "CiStatus",
    "DeliveryComposition",
    "DeliveryOutcome",
    "DeliveryService",
    "DeterministicCiReadinessService",
    "DeterministicDeliveryComposition",
    "DeterministicDeliveryService",
    "SubprocessDeliveryComposition",
    "SubprocessDeliveryService",
    "build_required_checks",
]


# Keep ``Mapping`` + ``BaseModel`` imports alive for callers that
# reach into the module for type hints.
_ = (Mapping, BaseModel)
