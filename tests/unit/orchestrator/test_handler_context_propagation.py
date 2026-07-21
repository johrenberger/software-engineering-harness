"""Tests for WP1 sub-task D — phase handlers return populated RunContext.

Cluster WP1 / story WP1.4.

The phase handlers used to return the input ``ctx`` unchanged, so the
``PipelineResult`` exposed only ``run_id`` + ``terminal_state`` +
``events``. After this change, each handler that produces an
artifact returns a new ``RunContext`` with the corresponding slot
populated (``profile_path`` / ``specification_path`` / ``plan_id`` /
``task_results`` / ``validation_exit_code`` / ``review_verdict`` /
``pr_url`` / ``ci_outcome``).

The handoff doc pins this down so callers (dashboard, controller)
can introspect run outputs without re-querying the ledger.

Tests are grouped by handler:
- handler-level: each phase handler returns the expected slot value
- end-to-end: PipelineResult.context reflects the union of all
  populated slots after a fresh run.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

# Ordering fix: import controller modules first to break the
# pre-existing orchestrator↔controller circular-import trap.
from seharness.controller.run_ledger import (  # noqa: F401
    PhaseCursor,
    RunLedger,
    RunState,
)
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.orchestrator import (
    _phase_ci,
    _phase_draft_pr,
    _phase_repository_discovery,
    _phase_review,
    _phase_specification,
    _phase_validation,
)
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


class _StubReviewService:
    """Test double for ``ReviewService``.

    WP3 (story H): review is invoked through a Protocol, so tests
    swap the attribute on the composition directly rather than
    patching the legacy ``_Reviewer`` staticmethod.
    """

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def review(self, *, review_ctx: Any) -> Any:
        return self._fn(review_ctx=review_ctx)


def _fresh_orchestrator(tmp_path: Path) -> tuple[Orchestrator, RunLedger]:
    """Build an orchestrator whose CI monitor + PR client are stubs
    so the test stays deterministic and fast."""
    ledger = RunLedger()
    cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
    orch = Orchestrator(
        run_ledger=ledger,
        config=cfg,
        pr_client=StubPullRequestClient(),
        ci_monitor=None,  # exercises the no-monitor branch in _phase_ci
        trace_writer=None,
    )
    return orch, ledger


def _ctx(tmp_path: Path, *, repo: Path | None = None) -> RunContext:
    """Build a minimal RunContext pointed at a temp directory."""
    repo_path = repo or (tmp_path / "repo")
    repo_path.mkdir(parents=True, exist_ok=True)
    return RunContext(
        run_id=RunId("orch-wp1h01"),
        feature_description="wp1 sub-task D handler context test",
        repo_path=str(repo_path),
    )


def _spec(phase: PhaseName) -> PhaseSpec:
    return PhaseSpec(run_id=RunId("orch-wp1h01"), phase=phase, attempt=0)


# ---------------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------------


class TestRepositoryDiscoveryPopulatesProfilePath:
    def test_profile_path_set(self, tmp_path: Path) -> None:
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_repository_discovery(
            orch, spec=_spec(PhaseName.REPOSITORY_DISCOVERY), ctx=ctx, run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.profile_path is not None
        # ``profile_path`` should point inside run_dir.
        assert new_ctx.profile_path.startswith(str(run_dir))


# ---------------------------------------------------------------------------
# Specification
# ---------------------------------------------------------------------------


class TestSpecificationPopulatesSpecificationPath:
    def test_specification_path_set(self, tmp_path: Path) -> None:
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_specification(
            orch, spec=_spec(PhaseName.SPECIFICATION), ctx=ctx, run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.specification_path is not None
        # Path should point at the actual file written by the handler.
        assert Path(new_ctx.specification_path).name == "specification.json"
        assert Path(new_ctx.specification_path).exists()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class TestPlanningPopulatesPlanId:
    def test_plan_id_set(self, tmp_path: Path) -> None:
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_repository_discovery(
            orch, spec=_spec(PhaseName.REPOSITORY_DISCOVERY), ctx=ctx, run_dir=run_dir
        )
        # The planning handler is wired before validation in the
        # canonical sequence; for this test we exercise the planning
        # handler directly with a profile_path set on ctx.
        from seharness.orchestrator.orchestrator import _phase_planning

        outcome, new_ctx, _ = _phase_planning(
            orch, spec=_spec(PhaseName.PLANNING), ctx=new_ctx, run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.plan_id is not None
        assert isinstance(new_ctx.plan_id, str)
        assert new_ctx.plan_id, "plan_id should be a non-empty string"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidationPopulatesExitCode:
    def test_validation_exit_code_always_set_when_command_runs(self, tmp_path: Path) -> None:
        """The default Plan has a pytest validation command. Whether
        the command passes or fails, ``validation_exit_code`` MUST be
        populated (never left as ``None``). This is the WP1.4
        contract: callers can branch on ``ctx.validation_exit_code``
        without re-running validation."""
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_validation(
            orch, spec=_spec(PhaseName.VALIDATION), ctx=ctx, run_dir=run_dir
        )
        # Outcome is OK or FAILED depending on whether pytest happens
        # to pass in the tmp dir. The assertion is the exit code
        # must be an int regardless.
        assert outcome in {PhaseOutcome.OK, PhaseOutcome.FAILED}
        assert new_ctx.validation_exit_code is not None, (
            "validation_exit_code must be set to an int whenever a "
            "validation command runs (WP1.4 contract)"
        )
        assert isinstance(new_ctx.validation_exit_code, int)
        # exit_code 0 = OK; non-zero = FAILED. If OK, exit_code is 0.
        if outcome == PhaseOutcome.OK:
            assert new_ctx.validation_exit_code == 0
        else:
            assert new_ctx.validation_exit_code != 0


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class TestReviewPopulatesVerdict:
    def test_review_verdict_always_set(self, tmp_path: Path) -> None:
        """The review handler always populates ``review_verdict`` —
        even on FAILED outcome — so callers can branch on it."""
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_review(
            orch, spec=_spec(PhaseName.REVIEW), ctx=ctx, run_dir=run_dir
        )
        # Default _Reviewer returns "approve" → OK with verdict set.
        assert outcome == PhaseOutcome.OK
        assert new_ctx.review_verdict == "approve"


# ---------------------------------------------------------------------------
# Draft PR
# ---------------------------------------------------------------------------


class TestDraftPRPopulatesPRURL:
    def test_pr_url_set(self, tmp_path: Path) -> None:
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_draft_pr(
            orch, spec=_spec(PhaseName.DRAFT_PR), ctx=ctx, run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.pr_url is not None
        assert isinstance(new_ctx.pr_url, str)


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------


class TestCIPopulatesOutcome:
    def test_no_monitor_sets_no_monitor_outcome(self, tmp_path: Path) -> None:
        """WP6 (Cluster H, story K): when ``_ci_monitor is None``
        AND a recorded head SHA exists, the deterministic
        ``_collect_ci_statuses`` synthesises a single success
        status and the CI outcome is ``"ready"``. The legacy
        ``"no_monitor"`` literal no longer appears in the
        orchestrator — the WP6 ``DeliveryComposition`` collapses
        the no-monitor condition into the same code path as
        development-profile runs."""
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(orch, spec=_spec(PhaseName.CI), ctx=ctx, run_dir=run_dir)
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "ready"


class TestCIBlockedWhenDeliveryShaMissing:
    """WP6: when ``delivery_head_sha`` is None the CI gate is
    blocked. This is the fail-closed branch — runs cannot advance
    past Phase 9 until a draft PR has been recorded."""

    def test_blocked_when_no_delivery_sha(self, tmp_path: Path) -> None:
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, detail = _phase_ci(
            orch, spec=_spec(PhaseName.CI), ctx=ctx, run_dir=run_dir
        )
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.ci_outcome == "blocked"
        assert "delivery head SHA missing" in detail


# ---------------------------------------------------------------------------
# PipelineResult.context end-to-end
# ---------------------------------------------------------------------------


class TestPipelineResultCarriesContext:
    def test_pipeline_result_context_set_after_run(self, tmp_path: Path) -> None:
        """End-to-end: a fresh run produces a ``PipelineResult`` with
        ``context`` populated. At minimum, the profile / spec / plan
        paths should be set (these are early-phase and always fire
        on a successful run)."""
        orch, _ = _fresh_orchestrator(tmp_path)
        result = orch.start_run(
            feature_description="wp1 sub-task D end-to-end context",
            repo_path=str(tmp_path / "repo"),
            run_id=RunId("orch-wp1ee01"),
        )
        assert result.context is not None, "PipelineResult.context should be populated"
        ctx = result.context
        # Profile + spec + plan are early-phase and must be set.
        assert ctx.profile_path is not None, "expected profile_path to be set"
        assert ctx.specification_path is not None, "expected specification_path to be set"
        assert ctx.plan_id is not None, "expected plan_id to be set"

    def test_pipeline_result_context_none_for_legacy_callers(self, tmp_path: Path) -> None:
        """PipelineResult.context defaults to None so existing code
        that constructs PipelineResult(run_id=..., terminal_state=...)
        (e.g. tests, fixtures) doesn't break."""
        from seharness.orchestrator.orchestrator import PipelineResult

        result = PipelineResult(run_id="r1", terminal_state="completed")
        assert result.context is None


# ---------------------------------------------------------------------------
# Missing-branch coverage for diff-cover (WP1.4 contract)
# ---------------------------------------------------------------------------
# These tests exercise the FAILED / SKIPPED / alternate branches of
# each handler that doesn't get hit on the canonical happy path.
# Without them, ``diff-cover --fail-under=80`` fails on PR2.


class TestValidationSkippedBranchSetsExitCodeNone:
    """Cluster WP1 / story WP1.4: when the plan has no validation
    commands, ``validation_exit_code`` is explicitly ``None`` (not
    left as the dataclass default), and the phase outcome is
    ``SKIPPED``."""

    def test_skipped_sets_exit_code_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patch _PlanBuilder to return a plan with empty
        validation_commands so the SKIPPED branch fires."""
        from seharness.artifacts.traceability import Plan, Task
        from seharness.orchestrator import orchestrator as orch_mod

        # Build a plan with an empty validation_commands list so
        # the handler's ``if not task.validation_commands`` branch
        # is taken.
        empty_plan = Plan(
            plan_id="plan-empty",
            tasks=(
                Task(
                    task_id="task-empty",
                    objective="empty",
                    requirement_traces=(),
                    allowed_paths=("src/",),
                    depends_on=(),
                    validation_commands=(),  # ← forces SKIPPED branch
                ),
            ),
        )

        original = orch_mod._PlanBuilder.build
        orch_mod._PlanBuilder.build = staticmethod(  # type: ignore[assignment]
            lambda *, ctx: empty_plan
        )
        try:
            orch, _ = _fresh_orchestrator(tmp_path)
            ctx = _ctx(tmp_path)
            run_dir = tmp_path / "runs" / "orch-wp1h01"
            run_dir.mkdir(parents=True, exist_ok=True)
            outcome, new_ctx, _ = _phase_validation(
                orch,
                spec=_spec(PhaseName.VALIDATION),
                ctx=ctx,
                run_dir=run_dir,
            )
        finally:
            orch_mod._PlanBuilder.build = original  # type: ignore[assignment]
        assert outcome == PhaseOutcome.SKIPPED
        assert new_ctx.validation_exit_code is None


class TestReviewFailedBranchSetsVerdict:
    """Cluster WP1 / story WP1.4: when the reviewer returns a non-approve
    verdict, ``review_verdict`` is still populated (never ``None``)
    and the phase outcome is ``FAILED``.

    WP3 (story H): review is now invoked through the
    ``ReviewService`` Protocol, so the patch targets the service
    attribute on the orchestrator rather than the legacy
    ``_Reviewer`` staticmethod.
    """

    def test_failed_branch_records_actual_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seharness.orchestrator.services import (
            ReviewContext,
            ReviewVerdict,
        )

        def _stub_review(*, review_ctx: ReviewContext) -> ReviewVerdict:
            return ReviewVerdict(
                status="changes_requested",
                approval=False,
                summary="test: request changes",
            )

        orch, _ = _fresh_orchestrator(tmp_path)
        original_review = orch._services.review
        orch._services.review = _StubReviewService(_stub_review)  # type: ignore[assignment]
        try:
            ctx = _ctx(tmp_path)
            run_dir = tmp_path / "runs" / "orch-wp1h01"
            run_dir.mkdir(parents=True, exist_ok=True)
            outcome, new_ctx, detail = _phase_review(
                orch,
                spec=_spec(PhaseName.REVIEW),
                ctx=ctx,
                run_dir=run_dir,
            )
        finally:
            orch._services.review = original_review
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.review_verdict == "request_changes"
        assert "request_changes" in detail


class TestCIMonitorNoRunMethod:
    """WP6 (Cluster H, story K): when the monitor lacks a ``run``
    method AND ``delivery_head_sha`` is set, the deterministic
    collector falls back to the synthetic-success path (matches
    the pre-WP6 ``"no_run_method"`` literal in semantics)."""

    def test_ci_monitor_without_run_method(self, tmp_path: Path) -> None:
        class _MonitorNoRun:
            """A monitor that has no ``run`` method."""

        orch, _ = _fresh_orchestrator(tmp_path)
        # Reach into the orchestrator's private slot to inject a
        # monitor that lacks ``run``.
        orch._ci_monitor = _MonitorNoRun()  # type: ignore[assignment]
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(
            orch,
            spec=_spec(PhaseName.CI),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        # WP6 collapses "no_run_method" / "no_view_factory" /
        # "no_view" / "no_monitor" into the synthetic-success
        # ready branch. The literal surfaces are deprecated; the
        # test pins the new contract.
        assert new_ctx.ci_outcome == "ready"


class TestCIMonitorNoViewFactory:
    """WP6: monitor with ``run`` but no ``_view_factory`` falls
    through to the deterministic synthetic-success path."""

    def test_ci_monitor_without_view_factory(self, tmp_path: Path) -> None:
        class _MonitorNoViewFactory:
            def run(self) -> None:  # pragma: no cover - not called
                return None

        orch, _ = _fresh_orchestrator(tmp_path)
        orch._ci_monitor = _MonitorNoViewFactory()  # type: ignore[assignment]
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(
            orch,
            spec=_spec(PhaseName.CI),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "ready"


class TestCIMonitorViewReturnsNone:
    """WP6: monitor with ``_view_factory`` returning ``None``
    falls through to the deterministic synthetic-success path."""

    def test_ci_view_factory_returns_none(self, tmp_path: Path) -> None:
        class _MonitorViewNone:
            def run(self) -> None:  # pragma: no cover - not called
                return None

            def _view_factory(self):  # noqa: ANN202 - protocol method
                return None

        orch, _ = _fresh_orchestrator(tmp_path)
        orch._ci_monitor = _MonitorViewNone()  # type: ignore[assignment]
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(
            orch,
            spec=_spec(PhaseName.CI),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "ready"


class TestCINotReadyFailedBranch:
    """WP6 (Cluster H, story K): when the CI view's statuses list
    lacks the recorded head SHA — or reports a non-success status
    for the recorded SHA — the CI outcome is ``'pending'`` and the
    phase outcome is ``FAILED``. The pre-WP6 ``ReadyEvaluator`` was
    replaced by the head-SHA matching ``CiReadinessService``."""

    def test_ci_not_ready_outcome(self, tmp_path: Path) -> None:

        class _MonitorWithStaleStatus:
            """A monitor whose view reports a stale success (head SHA
            does not match the recorded delivery head)."""

            def run(self) -> None:  # pragma: no cover - not called
                return None

            def _view_factory(self):  # noqa: ANN202
                return _View(
                    statuses=(
                        {
                            "name": "quality-gate",
                            "status": "success",
                            "head_sha": "old-sha-not-equal-to-recorded",
                        },
                    )
                )

        orch, _ = _fresh_orchestrator(tmp_path)
        orch._ci_monitor = _MonitorWithStaleStatus()  # type: ignore[assignment]
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(
            orch,
            spec=_spec(PhaseName.CI),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.FAILED
        # WP6 surfaces "stale" when the recorded head SHA does not
        # match any reported status's head_sha. The pre-WP6
        # "not_ready" literal was retired.
        assert new_ctx.ci_outcome == "stale"


class TestCIReadyBranch:
    """WP6: when the CI view reports success for every required
    check AND every status's ``head_sha`` matches the recorded
    delivery head SHA, the CI outcome is ``'ready'`` and the phase
    outcome is ``OK``."""

    def test_ci_ready_outcome(self, tmp_path: Path) -> None:
        class _MonitorWithMatchingStatus:
            def run(self) -> None:  # pragma: no cover - not called
                return None

            def _view_factory(self):  # noqa: ANN202
                return _View(
                    statuses=(
                        {
                            "name": "quality-gate",
                            "status": "success",
                            "head_sha": "deadbeef",
                        },
                    )
                )

        orch, _ = _fresh_orchestrator(tmp_path)
        orch._ci_monitor = _MonitorWithMatchingStatus()  # type: ignore[assignment]
        ctx = replace(_ctx(tmp_path), delivery_head_sha="deadbeef")
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(
            orch,
            spec=_spec(PhaseName.CI),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "ready"


class _View:
    """Minimal view object exposing a ``statuses`` tuple."""

    def __init__(self, *, statuses: tuple[dict[str, object], ...]) -> None:
        self.statuses = statuses
