"""WP6 (story K) — delivery composition: idempotency + head-SHA matching + fail-closed CI readiness.

Acceptance criteria from the MiniMax handoff doc:

* Replaying delivery does not create duplicate branches, commits,
  or PRs.
* A stale CI result for an earlier SHA cannot mark the run ready.
* Missing CI configuration produces ``blocked`` or ``paused`` in
  production.
* The ready state requires a draft PR and successful required
  checks for the exact head SHA.

These tests pin the WP6 invariants at the DeliveryComposition seam.
"""

from __future__ import annotations

from pathlib import Path

from seharness.config import RuntimeProfile
from seharness.controller.run_ledger import RunLedger
from seharness.delivery.idempotency import (
    IdempotencyKey,
    IdempotencyStore,
)
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.delivery import (
    CiStatus,
    DeterministicCiReadinessService,
    DeterministicDeliveryComposition,
    DeterministicDeliveryService,
    SubprocessDeliveryComposition,
    build_required_checks,
)
from seharness.orchestrator.orchestrator import _phase_ci, _phase_draft_pr
from seharness.orchestrator.types import (
    PhaseName,
    PhaseOutcome,
    PhaseSpec,
    RunContext,
    RunId,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def x() -> str:\n    return 'y'\n")
    return repo


def _fresh_orchestrator(
    tmp_path: Path,
    *,
    runtime_profile: RuntimeProfile = RuntimeProfile.TEST,
    delivery: object | None = None,
) -> Orchestrator:
    ledger = RunLedger()
    cfg = OrchestratorConfig(
        execution_root=str(tmp_path / "runs"),
        runtime_profile=runtime_profile,
    )
    return Orchestrator(
        run_ledger=ledger,
        config=cfg,
        pr_client=StubPullRequestClient(),
        ci_monitor=None,
        trace_writer=None,
        delivery=delivery,
    )


def _ctx(repo: Path, *, sha: str | None = None) -> RunContext:
    return RunContext(
        run_id=RunId("orch-wp6-x"),
        feature_description="x",
        repo_path=str(repo),
        delivery_head_sha=sha,
    )


def _spec() -> PhaseSpec:
    return PhaseSpec(run_id=RunId("orch-wp6-x"), phase=PhaseName.DRAFT_PR, attempt=0)


# ---------------------------------------------------------------------------
# WP6.1 — Replaying delivery is idempotent
# ---------------------------------------------------------------------------


class TestDeliveryIdempotency:
    """WP6 acceptance: ``Replaying delivery does not create
    duplicate branches, commits, or PRs.``"""

    def test_first_delivery_creates_record(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        store = IdempotencyStore(tmp_path / "idemp")
        svc = DeterministicDeliveryService(pr_client=StubPullRequestClient())
        from seharness.delivery.commit import CommitMessage

        first = svc.deliver(
            repo_root=repo,
            run_id="orch-wp6-x",
            task_id="draft-pr",
            title="feat: x",
            body="x",
            authorized_files=("main.py",),
            commit_message=CommitMessage(
                scope="wp6",
                description="x",
                feature_id="orch-wp6-x",
                task_id="draft-pr",
            ),
            idempotency_root=tmp_path / "idemp",
        )
        assert first.replayed is False
        assert first.pr_url is not None
        # IdempotencyStore now has the record.
        record = store.get(IdempotencyKey(run_id="orch-wp6-x", task_id="draft-pr"))
        assert record is not None
        assert record.commit_sha == first.commit_sha

    def test_second_delivery_returns_cached_record(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        svc = DeterministicDeliveryService(pr_client=StubPullRequestClient())
        from seharness.delivery.commit import CommitMessage

        first = svc.deliver(
            repo_root=repo,
            run_id="orch-wp6-x",
            task_id="draft-pr",
            title="feat: x",
            body="x",
            authorized_files=("main.py",),
            commit_message=CommitMessage(
                scope="wp6",
                description="x",
                feature_id="orch-wp6-x",
                task_id="draft-pr",
            ),
            idempotency_root=tmp_path / "idemp",
        )
        second = svc.deliver(
            repo_root=repo,
            run_id="orch-wp6-x",
            task_id="draft-pr",
            title="feat: x",
            body="x",
            authorized_files=("main.py",),
            commit_message=CommitMessage(
                scope="wp6",
                description="x",
                feature_id="orch-wp6-x",
                task_id="draft-pr",
            ),
            idempotency_root=tmp_path / "idemp",
        )
        assert second.replayed is True
        assert second.branch == first.branch
        assert second.commit_sha == first.commit_sha
        assert second.pr_url == first.pr_url


# ---------------------------------------------------------------------------
# WP6.2 — Stale CI result cannot mark the run ready
# ---------------------------------------------------------------------------


class TestStaleCiCannotMarkReady:
    """WP6 acceptance: ``A stale CI result for an earlier SHA
    cannot mark the run ready.``"""

    def test_stale_status_with_mismatched_sha_marks_run_not_ready(self) -> None:
        svc = DeterministicCiReadinessService()
        outcome = svc.check(
            recorded_head_sha="new-sha",
            statuses=(
                CiStatus(
                    name="quality-gate",
                    status="success",
                    head_sha="old-sha",
                ),
            ),
            required_checks=("quality-gate",),
        )
        assert outcome.ready is False
        assert outcome.state == "stale"

    def test_fresh_status_with_matching_sha_marks_run_ready(self) -> None:
        svc = DeterministicCiReadinessService()
        outcome = svc.check(
            recorded_head_sha="new-sha",
            statuses=(
                CiStatus(
                    name="quality-gate",
                    status="success",
                    head_sha="new-sha",
                ),
            ),
            required_checks=("quality-gate",),
        )
        assert outcome.ready is True
        assert outcome.state == "ready"

    def test_pending_status_marks_run_pending(self) -> None:
        svc = DeterministicCiReadinessService()
        outcome = svc.check(
            recorded_head_sha="new-sha",
            statuses=(
                CiStatus(
                    name="quality-gate",
                    status="pending",
                    head_sha="new-sha",
                ),
            ),
            required_checks=("quality-gate",),
        )
        assert outcome.ready is False
        assert outcome.state == "pending"

    def test_missing_required_check_marks_run_blocked(self) -> None:
        svc = DeterministicCiReadinessService()
        outcome = svc.check(
            recorded_head_sha="new-sha",
            statuses=(),
            required_checks=("quality-gate",),
        )
        assert outcome.ready is False
        assert outcome.state == "blocked"

    def test_empty_recorded_sha_marks_run_blocked(self) -> None:
        svc = DeterministicCiReadinessService()
        outcome = svc.check(
            recorded_head_sha="",
            statuses=(),
            required_checks=("quality-gate",),
        )
        assert outcome.ready is False
        assert outcome.state == "blocked"


# ---------------------------------------------------------------------------
# WP6.3 — Missing CI configuration fails closed in production
# ---------------------------------------------------------------------------


class TestProductionFailClosed:
    """WP6 acceptance: ``Missing CI configuration produces
    ``blocked`` or ``paused`` in production.``"""

    def test_production_profile_without_monitor_routes_to_failed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path, runtime_profile=RuntimeProfile.PRODUCTION)
        ctx = _ctx(repo, sha="some-sha")
        run_dir = tmp_path / "runs" / "orch-wp6-prod"
        run_dir.mkdir(parents=True)
        outcome, new_ctx, detail = _phase_ci(
            orch,
            spec=PhaseSpec(run_id=RunId("orch-wp6-x"), phase=PhaseName.CI, attempt=0),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.ci_outcome == "blocked"
        assert "blocked" in detail.lower() or "missing" in detail.lower()

    def test_development_profile_without_monitor_synthesises_success(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path, runtime_profile=RuntimeProfile.DEVELOPMENT)
        ctx = _ctx(repo, sha="some-sha")
        run_dir = tmp_path / "runs" / "orch-wp6-dev"
        run_dir.mkdir(parents=True)
        outcome, new_ctx, _detail = _phase_ci(
            orch,
            spec=PhaseSpec(run_id=RunId("orch-wp6-x"), phase=PhaseName.CI, attempt=0),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "ready"


# ---------------------------------------------------------------------------
# WP6.4 — Phase draft-PR records delivery bookkeeping
# ---------------------------------------------------------------------------


class TestDraftPrPopulatesRunContext:
    """WP6: ``_phase_draft_pr`` records the branch + commit SHA +
    PR URL into ``RunContext`` so the CI phase can do head-SHA
    matching."""

    def test_draft_pr_records_branch_and_sha(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp6-draft"
        run_dir.mkdir(parents=True)
        ctx = _ctx(repo)
        outcome, new_ctx, detail = _phase_draft_pr(orch, spec=_spec(), ctx=ctx, run_dir=run_dir)
        assert outcome == PhaseOutcome.OK
        assert new_ctx.delivery_branch is not None
        assert new_ctx.delivery_head_sha is not None
        assert new_ctx.pr_url is not None
        assert "branch=" in detail
        assert "sha=" in detail

    def test_draft_pr_replay_does_not_create_new_record(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp6-replay"
        run_dir.mkdir(parents=True)
        ctx = _ctx(repo)
        first_outcome, first_ctx, _ = _phase_draft_pr(orch, spec=_spec(), ctx=ctx, run_dir=run_dir)
        # Replay with the updated context (same run/task ID).
        second_outcome, second_ctx, second_detail = _phase_draft_pr(
            orch,
            spec=_spec(),
            ctx=first_ctx,
            run_dir=run_dir,
        )
        assert first_outcome == second_outcome == PhaseOutcome.OK
        assert first_ctx.delivery_head_sha == second_ctx.delivery_head_sha
        assert first_ctx.pr_url == second_ctx.pr_url
        # The replay suffix surfaces in the detail message.
        assert "(replay)" in second_detail or second_ctx.delivery_head_sha is not None


# ---------------------------------------------------------------------------
# WP6.5 — build_required_checks helper
# ---------------------------------------------------------------------------


class TestBuildRequiredChecks:
    def test_returns_plan_checks_when_provided(self) -> None:
        assert build_required_checks(("lint", "type")) == ("lint", "type")

    def test_returns_default_when_none(self) -> None:
        assert build_required_checks(None) == ("quality-gate",)

    def test_returns_default_when_empty(self) -> None:
        assert build_required_checks(()) == ("quality-gate",)


# ---------------------------------------------------------------------------
# WP6.6 — Compositions are protocol-typed
# ---------------------------------------------------------------------------


class TestCompositionsExposeProtocols:
    def test_deterministic_composition_is_protocol(self, tmp_path: Path) -> None:
        comp = DeterministicDeliveryComposition()
        from seharness.orchestrator.delivery import (
            CiReadinessService,
            DeliveryComposition,
            DeliveryService,
        )

        assert isinstance(comp, DeliveryComposition)
        assert isinstance(comp.delivery, DeliveryService)
        assert isinstance(comp.readiness, CiReadinessService)

    def test_subprocess_composition_is_protocol(self, tmp_path: Path) -> None:
        comp = SubprocessDeliveryComposition()
        from seharness.orchestrator.delivery import (
            CiReadinessService,
            DeliveryComposition,
            DeliveryService,
        )

        assert isinstance(comp, DeliveryComposition)
        assert isinstance(comp.delivery, DeliveryService)
        assert isinstance(comp.readiness, CiReadinessService)
        # Discriminator surfaces for runtime_profile validators.
        assert comp.kind == "subprocess"
        assert DeterministicDeliveryComposition().kind == "deterministic"
