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

from pathlib import Path

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
        """When ``_ci_monitor is None`` the CI outcome is the literal
        string ``"no_monitor"`` (not ``None``). This is the SPEC
        §"Phase 9" phrase; callers can branch on it to detect
        runs that passed without CI evidence."""
        orch, _ = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp1h01"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_ci(orch, spec=_spec(PhaseName.CI), ctx=ctx, run_dir=run_dir)
        assert outcome == PhaseOutcome.OK
        assert new_ctx.ci_outcome == "no_monitor"


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
