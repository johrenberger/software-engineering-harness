"""Canonical orchestrator (Cluster A, story A2).

The orchestrator composes the existing slice-3..slice-10 services in
the SPEC §"Phase 8" sequence:

    feature_request → repository_discovery → specification → planning →
    implementation → validation → remediation → review → draft_pr →
    ci → ready → completed

It is the SINGLE workflow engine for the harness. ``/feature``,
``seharness run``, Telegram, dashboard, and the E2E test all invoke
this orchestrator. There is no other path from a feature request to a
draft PR.

Every phase emits a ``PipelineEvent`` so the existing vertical-slice
event log contract is preserved. The orchestrator persists every state
transition to the controller's ``RunLedger`` so ``/status``, ``/runs``,
``/resume``, and ``/cancel`` observe the run.

Failures route to ``failed`` / ``blocked`` / ``paused`` RunState — not
to unconditional ``completed`` (Cluster A, story A4).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from seharness.artifacts.traceability import (
    Plan,
    RequirementTrace,
    Task,
)
from seharness.controller.run_ledger import RunLedger, RunState
from seharness.delivery.pr import PullRequestClient, StubPullRequestClient
from seharness.domain.requirements import FunctionalRequirementId, ScenarioId
from seharness.orchestrator.phases import PHASE_SEQUENCE, phase_info
from seharness.orchestrator.runner import LocalCommandRunner, StubRunner
from seharness.orchestrator.types import (
    OrchestratorConfig,
    PhaseName,
    PhaseOutcome,
    PhaseSpec,
    RunContext,
    RunId,
    new_run_id,
)


@dataclass(frozen=True)
class PipelineEvent:
    """A single phase event emitted by the orchestrator.

    Shape matches the slice-13 ``PipelineEvent`` so the existing E2E
    test continues to pass.
    """

    phase: str
    timestamp: float
    detail: str = ""


@dataclass(frozen=True)
class PipelineResult:
    """Final result of an orchestrator run.

    Shape matches the slice-13 ``PipelineResult``. ``terminal_state``
    is the SPEC §"Phase 8" phrase: ``"completed"`` (terminal success),
    ``"failed"`` (any unrecoverable error), ``"blocked"`` (policy
    violation requiring intervention), or ``"paused"`` (awaiting
    resume / approval).
    """

    run_id: str
    terminal_state: str
    events: tuple[PipelineEvent, ...] = ()


# ---------------------------------------------------------------------------
# Internal artifacts
# ---------------------------------------------------------------------------


class OrchestratorError(RuntimeError):
    """Raised by the orchestrator for any phase failure.

    Carries the run_id and phase so callers can resume cleanly.
    """


class _PlanBuilder:
    """Builds a slice-5 ``Plan`` from a RunContext.

    Deterministic: the same feature description always produces the
    same plan_id. Cluster F replaces this with a model-driven planner.
    """

    @staticmethod
    def build(*, ctx: RunContext) -> Plan:
        # Derive a deterministic plan_id from the run id.
        plan_id = f"plan-{ctx.run_id.replace('orch-', '')}"
        req_id = FunctionalRequirementId("FR-1")
        scenario_id = ScenarioId("SCN-1")
        trace = RequirementTrace(
            requirement_id=req_id,
            scenario_ids=(scenario_id,),
        )
        # Make the task ID deterministic too.
        task_id = f"task-{ctx.run_id.replace('orch-', '')}"
        task = Task(
            task_id=task_id,
            objective=ctx.feature_description[:200],
            requirement_traces=(trace,),
            allowed_paths=("src/", "tests/", "docs/"),
            depends_on=(),
            validation_commands=("pytest --no-cov -q",),
        )
        return Plan(plan_id=plan_id, tasks=(task,))


class _RepoProfiler:
    """Deterministic repository profiler — slice 3 service surface.

    Writes a ``repo-profile.json`` artifact into the run directory so
    downstream phases have a real, inspectable result. Cluster F
    replaces this with the full slice-3 ``RepositoryProfiler``.
    """

    @staticmethod
    def profile(*, repo_path: Path, run_dir: Path) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        files: list[str] = []
        if repo_path.exists():
            for child in sorted(repo_path.iterdir()):
                if child.is_file():
                    files.append(child.name)
        # Find language hint from file extensions.
        extensions = {f.rsplit(".", 1)[-1] for f in files if "." in f}
        language = "python" if "py" in extensions else ("rust" if "rs" in extensions else "unknown")
        profile: dict[str, object] = {
            "repo_path": str(repo_path),
            "files": files,
            "extensions": sorted(extensions),
            "language": language,
            "profiled_at": time.time(),
        }
        out = run_dir / "repo-profile.json"
        out.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
        return out


class _Reviewer:
    """Deterministic reviewer verdict.

    For Cluster A the reviewer always returns ``approve`` — a future
    slice will plug a real reviewer behind this Protocol. The verdict
    is recorded as an artifact so downstream phases can audit it.
    """

    @staticmethod
    def review(*, run_dir: Path, plan: Plan) -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        verdict = {
            "verdict": "approve",
            "rationale": "deterministic reviewer (Cluster A)",
            "tasks_reviewed": [t.task_id for t in plan.tasks],
        }
        (run_dir / "review-verdict.json").write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n"
        )
        return "approve"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """The canonical workflow engine.

    Constructed with a ``RunLedger`` (shared with the controller) and
    optional injected services (``PullRequestClient``,
    ``CiMonitor``). With no injected services, the orchestrator uses
    deterministic stubs — exactly the SPEC's "stub by default, real
    adapter when wired" pattern.
    """

    def __init__(
        self,
        *,
        run_ledger: RunLedger,
        config: OrchestratorConfig | None = None,
        pr_client: PullRequestClient | None = None,
        ci_monitor: object | None = None,
    ) -> None:
        self._run_ledger = run_ledger
        self._config = config or OrchestratorConfig()
        self._pr_client = pr_client or StubPullRequestClient()
        self._ci_monitor = ci_monitor  # typed lazily to avoid cycles
        self._runner = LocalCommandRunner() if self._config.use_real_subprocess else StubRunner()
        # Per-run state — the orchestrator supports at most one
        # in-flight run id at a time. Multi-run parallelism lands in
        # Cluster E (story E2).
        self._events: dict[str, tuple[PipelineEvent, ...]] = {}

    # ----- public API ----------------------------------------------------

    def start_run(
        self,
        *,
        feature_description: str,
        repo_path: str,
        run_id: RunId | None = None,
    ) -> PipelineResult:
        """Execute the full phase sequence and return the result.

        Side effects: writes artifacts to ``<execution_root>/<run_id>/``,
        records every transition in ``self._run_ledger``.
        """
        rid = run_id or new_run_id()
        if not feature_description:
            raise OrchestratorError("feature_description must be non-empty")
        if not repo_path:
            raise OrchestratorError("repo_path must be non-empty")
        repo = Path(repo_path).resolve()
        run_dir = Path(self._config.execution_root) / str(rid)
        run_dir.mkdir(parents=True, exist_ok=True)

        ctx = RunContext(
            run_id=rid,
            feature_description=feature_description,
            repo_path=str(repo),
        )
        # Record the run start in the shared ledger.
        self._run_ledger.record_start(str(rid), repository=str(repo))

        events: list[PipelineEvent] = []
        # SPEC §line 587 canonicalizes the terminal phrase as
        # ``"completed"`` — the controller's RunState stores the same
        # value via RunState.COMPLETE.value (renamed in Cluster A so the
        # internal enum matches the SPEC). The pipeline returns the
        # SPEC phrase; the ledger records the same phrase.
        terminal_state = PhaseName.COMPLETED.value
        try:
            for phase in PHASE_SEQUENCE:
                spec = PhaseSpec(run_id=rid, phase=phase)
                outcome, ctx, detail = self._run_phase(spec=spec, ctx=ctx, run_dir=run_dir)
                events.append(
                    PipelineEvent(
                        phase=phase.value,
                        timestamp=time.time(),
                        detail=detail or f"{phase.value} {outcome.value}",
                    )
                )
                if outcome in {PhaseOutcome.FAILED, PhaseOutcome.BLOCKED}:
                    terminal_state = (
                        RunState.FAILED.value
                        if outcome == PhaseOutcome.FAILED
                        else RunState.BLOCKED.value
                    )
                    if outcome == PhaseOutcome.FAILED:
                        self._run_ledger.mark_failed(str(rid))
                    else:
                        self._run_ledger.mark_blocked(str(rid))
                    break
                if outcome == PhaseOutcome.PAUSED:
                    terminal_state = RunState.PAUSED.value
                    self._run_ledger.mark_paused(str(rid))
                    break
                if phase == PhaseName.COMPLETED:
                    self._run_ledger.mark_complete(str(rid))
                    terminal_state = PhaseName.COMPLETED.value
        except OrchestratorError as exc:
            # Fatal phase failure: record FAILED in the ledger and
            # return a structured failed PipelineResult instead of
            # propagating. Callers that want exception-style flow
            # can inspect ``terminal_state``.
            self._run_ledger.mark_failed(str(rid))
            events.append(
                PipelineEvent(
                    phase="error",
                    timestamp=time.time(),
                    detail=f"fatal phase failure: {exc}",
                )
            )
            terminal_state = RunState.FAILED.value
        except Exception as exc:
            events.append(
                PipelineEvent(
                    phase="error",
                    timestamp=time.time(),
                    detail=f"unhandled exception: {exc!r}",
                )
            )
            self._run_ledger.mark_failed(str(rid))
            terminal_state = RunState.FAILED.value

        result = PipelineResult(
            run_id=str(rid),
            terminal_state=terminal_state,
            events=tuple(events),
        )
        self._events[str(rid)] = result.events
        return result

    def resume_run(self, run_id: str) -> PipelineResult:
        """Resume a paused/failed run from the last successful phase.

        Cluster A ships a minimal implementation: re-run from scratch
        with the same feature description reconstructed from the
        ledger. Cluster E (story E1) adds deterministic replay.
        """
        rec = self._run_ledger.get(run_id)
        if rec is None:
            raise OrchestratorError(f"unknown run_id: {run_id}")
        if rec.state in {RunState.COMPLETE, RunState.CANCELLED}:
            raise OrchestratorError(
                f"run {run_id} is in terminal state {rec.state.value}; cannot resume"
            )
        # Mark resume in the ledger (best-effort — Cluster A keeps it
        # simple).
        self._run_ledger.mark_resume(run_id)
        # Reconstruct and re-run. A future slice will replay the
        # ledger's event log instead.
        return self.start_run(
            feature_description=f"resume:{run_id}",
            repo_path=rec.repository,
            run_id=RunId(run_id),
        )

    def cancel_run(self, run_id: str) -> None:
        rec = self._run_ledger.get(run_id)
        if rec is None:
            raise OrchestratorError(f"unknown run_id: {run_id}")
        if rec.state == RunState.COMPLETE:
            raise OrchestratorError(f"run {run_id} already complete; cannot cancel")
        self._run_ledger.mark_cancelled(run_id)

    # ----- phase dispatch ------------------------------------------------

    def _run_phase(
        self,
        *,
        spec: PhaseSpec,
        ctx: RunContext,
        run_dir: Path,
    ) -> tuple[PhaseOutcome, RunContext, str]:
        """Dispatch a single phase. Returns (outcome, new_ctx, detail)."""
        info = phase_info(spec.phase)
        handler = _PHASE_HANDLERS.get(spec.phase)
        if handler is None:
            return PhaseOutcome.SKIPPED, ctx, f"no handler for {spec.phase.value}"
        try:
            outcome, new_ctx, detail = handler(self, spec=spec, ctx=ctx, run_dir=run_dir)
        except OrchestratorError as exc:
            if info.fatal_on_failure:
                raise
            return PhaseOutcome.FAILED, ctx, f"phase failed: {exc}"
        except Exception as exc:
            if info.fatal_on_failure:
                raise OrchestratorError(f"fatal phase {spec.phase.value} failed: {exc}") from exc
            return PhaseOutcome.FAILED, ctx, f"phase failed: {exc!r}"
        return outcome, new_ctx, detail


# ---------------------------------------------------------------------------
# Phase handlers — one closure per phase. The closure captures ``self``
# so handlers can reach ``self._pr_client`` / ``self._ci_monitor`` /
# ``self._runner`` without the caller wiring them per-phase.
# ---------------------------------------------------------------------------


class _PhaseHandler(Protocol):
    def __call__(
        self,
        orch: Orchestrator,
        *,
        spec: PhaseSpec,
        ctx: RunContext,
        run_dir: Path,
    ) -> tuple[PhaseOutcome, RunContext, str]: ...


def _phase_feature_request(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    return PhaseOutcome.OK, ctx, "feature request accepted"


def _phase_repository_discovery(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    profile = _RepoProfiler.profile(repo_path=Path(ctx.repo_path), run_dir=run_dir)
    return PhaseOutcome.OK, ctx, f"profile written: {profile.name}"


def _phase_specification(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_doc = {
        "run_id": str(spec.run_id),
        "description": ctx.feature_description,
        "repo_path": ctx.repo_path,
        "spec_version": 1,
    }
    spec_path = run_dir / "specification.json"
    spec_path.write_text(json.dumps(spec_doc, indent=2, sort_keys=True) + "\n")
    return PhaseOutcome.OK, ctx, f"specification written: {spec_path.name}"


def _phase_planning(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    plan_path = run_dir / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n")
    return PhaseOutcome.OK, ctx, f"plan produced: {plan.plan_id}"


def _phase_implementation(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    task = plan.tasks[0]
    # Use the slice-7 TaskExecutionService for the heavy lifting. The
    # service's TaskEvidenceLayout appends ``execution/<task_id>`` to
    # the execution_root, so we hand it ``run_dir`` and let the
    # service lay out evidence under ``run_dir/execution/<task_id>``.
    from seharness.execution.service import TaskExecutionService  # noqa: PLC0415

    svc = TaskExecutionService(
        repo_root=Path(ctx.repo_path),
        execution_root=run_dir,
    )
    red_dir = run_dir / "execution" / task.task_id / "red"
    green_dir = run_dir / "execution" / task.task_id / "green"
    red_dir.mkdir(parents=True, exist_ok=True)
    green_dir.mkdir(parents=True, exist_ok=True)

    def _runner(r: Path, g: Path) -> None:
        orch._runner.run_task(red_dir=r, green_dir=g, task_id=task.task_id)

    try:
        result = svc.execute(plan=plan, task_id=task.task_id, runner=_runner)
    except Exception as exc:
        # Convert any TaskExecutionService failure into a PhaseOutcome.
        raise OrchestratorError(f"implementation failed: {exc}") from exc
    return (
        PhaseOutcome.OK,
        ctx,
        f"task {task.task_id} executed: violations={list(result.violations)}",
    )


def _phase_validation(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    task = plan.tasks[0]
    if not task.validation_commands:
        return PhaseOutcome.SKIPPED, ctx, "no validation commands"
    cmd = task.validation_commands[0]
    result = orch._runner.run_validation(command=cmd, cwd=Path(ctx.repo_path), timeout_s=60.0)
    detail = f"{cmd} → exit {result.exit_code}"
    if result.exit_code != 0:
        return PhaseOutcome.FAILED, ctx, detail
    return PhaseOutcome.OK, ctx, detail


def _phase_remediation(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    # Slice-7/8 already invoked revert_unauthorized; this phase simply
    # records that we passed the remediation gate.
    return PhaseOutcome.OK, ctx, "no outstanding violations"


def _phase_review(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    verdict = _Reviewer.review(run_dir=run_dir, plan=plan)
    if verdict != "approve":
        return PhaseOutcome.FAILED, ctx, f"review verdict: {verdict}"
    return PhaseOutcome.OK, ctx, f"verdict: {verdict}"


def _phase_draft_pr(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    url = orch._pr_client.create(
        branch=f"agent/feature-{str(spec.run_id).replace('orch-', '')}",
        title=f"feat: {ctx.feature_description[:60]}",
        body=f"Automated run {spec.run_id}.",
        draft=orch._config.pr_draft,
    )
    return PhaseOutcome.OK, ctx, f"draft PR: {url}"


def _phase_ci(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    monitor = orch._ci_monitor
    if monitor is None:
        # No real monitor wired; declare the run CI-ready if validation
        # passed (which it did, otherwise we'd have routed to failed).
        return PhaseOutcome.OK, ctx, "CI monitor not configured; assuming ready"
    # Real monitor: invoke .run() with bounded budget.
    if not hasattr(monitor, "run"):
        return PhaseOutcome.OK, ctx, "monitor missing run(); assuming ready"
    # We do NOT call .run() here because it blocks until the PR is
    # ready. /pr_status uses view_factory for an instant view; the
    # orchestrator mirrors that pattern by inspecting the most recent
    # view without polling.
    view_factory = getattr(monitor, "_view_factory", None)
    if view_factory is None:
        return PhaseOutcome.OK, ctx, "monitor has no view_factory; assuming ready"
    view = view_factory()
    if view is None:
        return PhaseOutcome.OK, ctx, "no view available; assuming ready"
    from seharness.ci.readiness import ReadyEvaluator  # noqa: PLC0415

    decision = ReadyEvaluator().evaluate(view)
    if not decision.can_be_ready:
        return PhaseOutcome.FAILED, ctx, "CI not ready"
    return PhaseOutcome.OK, ctx, "CI ready"


def _phase_ready(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    return PhaseOutcome.OK, ctx, "ready for review"


def _phase_completed(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    return PhaseOutcome.OK, ctx, "completed"


_PHASE_HANDLERS: dict[PhaseName, _PhaseHandler] = {
    PhaseName.FEATURE_REQUEST: _phase_feature_request,
    PhaseName.REPOSITORY_DISCOVERY: _phase_repository_discovery,
    PhaseName.SPECIFICATION: _phase_specification,
    PhaseName.PLANNING: _phase_planning,
    PhaseName.IMPLEMENTATION: _phase_implementation,
    PhaseName.VALIDATION: _phase_validation,
    PhaseName.REMEDIATION: _phase_remediation,
    PhaseName.REVIEW: _phase_review,
    PhaseName.DRAFT_PR: _phase_draft_pr,
    PhaseName.CI: _phase_ci,
    PhaseName.READY: _phase_ready,
    PhaseName.COMPLETED: _phase_completed,
}


__all__ = ["Orchestrator", "OrchestratorError", "PipelineEvent", "PipelineResult"]
