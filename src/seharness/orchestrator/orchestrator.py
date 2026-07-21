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

import contextlib
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from seharness.artifacts.traceability import (
    Plan,
    RequirementTrace,
    Task,
)
from seharness.controller.run_ledger import RunLedger, RunRecord, RunState, to_jsonable
from seharness.delivery.pr import PullRequestClient, StubPullRequestClient
from seharness.domain.requirements import FunctionalRequirementId, ScenarioId
from seharness.observability.trace import (
    ArtifactProduced,
    PhaseCompleted,
    PhaseFailed,
    PhaseStarted,
    Trace,
    TraceEvent,
    TraceWriter,
)
from seharness.orchestrator.phases import PHASE_SEQUENCE, phase_info
from seharness.orchestrator.runner import LocalCommandRunner, StubRunner
from seharness.orchestrator.runtime_profile import (
    iter_adapter_slots,
    validate_runtime_profile_adapters,
)
from seharness.orchestrator.services import (
    DeterministicServiceComposition,
    ImplementationOutcome,
    ReviewContext,
    ReviewVerdict,
    ServiceComposition,
    SpecificationArtifact,
)
from seharness.orchestrator.types import (
    OrchestratorConfig,
    PhaseName,
    PhaseOutcome,
    PhaseSpec,
    RunContext,
    RunId,
    new_run_id,
)
from seharness.sandbox.cancellation import CancellationToken


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

    Cluster WP1 / story WP1.4: ``context`` carries the final
    :class:`RunContext` populated by the last successful phase, so
    callers can introspect ``profile_path`` / ``specification_path``
    / ``plan_id`` / ``review_verdict`` / ``pr_url`` / ``ci_outcome``
    without re-querying the ledger. ``None`` for callers that
    intentionally want a lighter payload (and for legacy code paths
    that pre-date the populated-context wire-up).
    """

    run_id: str
    terminal_state: str
    events: tuple[PipelineEvent, ...] = ()
    context: RunContext | None = None


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
# WP3 helpers — adapters between the new ``ReviewVerdict`` /
# ``ImplementationOutcome`` shapes and the legacy ``_phase_review``
# string contract.
# ---------------------------------------------------------------------------


def _verdict_to_legacy(verdict: ReviewVerdict) -> str:
    """Map the closed-set :class:`ReviewVerdict` status back to the
    legacy string used by ``RunContext.review_verdict`` and the
    downstream controller commands.

    The mapping preserves the legacy ``approve`` / ``request_changes``
    / ``reject`` vocabulary so existing tests + the Telegram
    controller keep working unchanged.
    """
    if verdict.status == "approved":
        return "approve"
    if verdict.status == "rejected":
        return "reject"
    return "request_changes"


def _build_review_spec(ctx: RunContext, run_dir: Path) -> SpecificationArtifact:
    """Construct a minimal :class:`SpecificationArtifact` from the run
    context for the review service to consume.

    WP3 requires review to receive *only* the approved spec, the
    diff, the plan, and the validation/coverage results — never the
    chat history. ``SpecificationArtifact`` carries exactly those
    fields so review can render them without re-reading the run
    directory.
    """
    return SpecificationArtifact(
        spec_version=1,
        description=ctx.feature_description or "",
        repo_path=ctx.repo_path or "",
        run_id=str(ctx.run_id),
    )


# Sentinel ``ImplementationOutcome`` used when the orchestrator has
# not yet recorded a real prior outcome. ``_phase_remediation``
# passes this in so the deterministic remediation service has
# something to inspect; it always returns ``not_applicable``.
_ZERO_OUTCOME = ImplementationOutcome(
    attempted=False,
    attempt_index=0,
    final_response=None,
    structured=None,
    error_kind=None,
    error_message=None,
)


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
        trace_writer: TraceWriter | None | str = "auto",
        services: ServiceComposition | None = None,
    ) -> None:
        self._run_ledger = run_ledger
        self._config = config or OrchestratorConfig()
        self._pr_client = pr_client or StubPullRequestClient()
        self._ci_monitor = ci_monitor  # typed lazily to avoid cycles
        # Cluster WP3 (story H): provider-neutral service composition.
        # Default is the deterministic composition so existing
        # callers (tests, sandbox profile) keep working unchanged.
        self._services: ServiceComposition = services or DeterministicServiceComposition()
        self._runner = LocalCommandRunner() if self._config.use_real_subprocess else StubRunner()
        # Cluster WP2: fail-closed adapter validation. In ``PRODUCTION``
        # we refuse to start with any stub-class-named adapter; in
        # ``DEVELOPMENT`` we return a diagnostic so the caller can log
        # a single startup warning; in ``TEST`` we silently pass. The
        # validator is invoked here (rather than at adapter wire time)
        # so the slots are mutable up to this point and so we can
        # enumerate every slot via :func:`iter_adapter_slots`.
        validate_runtime_profile_adapters(
            profile=self._config.runtime_profile,
            adapters=dict(iter_adapter_slots(self)),
        )
        # Trace writer: ``"auto"`` defers to per-run creation at
        # ``<execution_root>/<run_id>/trace.jsonl``; ``None`` disables
        # tracing entirely; an explicit instance is used as-is.
        self._trace_writer_default = trace_writer
        self._trace_writer_active: TraceWriter | None = None
        # Per-run state — the orchestrator supports at most one
        # in-flight run id at a time. Multi-run parallelism lands in
        # Cluster E (story E2).
        self._events: dict[str, tuple[PipelineEvent, ...]] = {}
        # Per-run cancellation tokens. Cluster E, story E4b:
        # ``start_run`` registers a fresh token keyed by run_id;
        # ``cancel_run`` flips it (which triggers SIGTERM/SIGKILL
        # escalation in the runner); the registry entry is removed
        # when the run finishes. Multi-run parallelism lands in
        # Cluster E (story E2).
        self._cancel_tokens: dict[str, CancellationToken] = {}

    # ----- public API ----------------------------------------------------

    def start_run(  # noqa: PLR0912,PLR0915  # start_run is a workflow driver with many branches
        self,
        *,
        feature_description: str,
        repo_path: str,
        run_id: RunId | None = None,
        idempotency_key: str = "",
        resume_from_run_id: str | None = None,
    ) -> PipelineResult:
        """Execute the full phase sequence and return the result.

        Side effects: writes artifacts to ``<execution_root>/<run_id>/``,
        records every transition in ``self._run_ledger``.

        Cluster E1 (story E1): ``idempotency_key`` is a stable
        identifier for the *logical request* (e.g. ``"pr-123-v2"``,
        ``"claude-session-<uuid>"``). If a prior run with the same
        key exists in the ledger, ``OrchestratorError`` is raised
        before any phase fires — the caller is expected to look up
        the existing run and ``resume_run`` it instead of asking
        for a fresh start. An empty key disables dedupe.
        """
        rid = run_id or new_run_id()
        if not feature_description:
            raise OrchestratorError("feature_description must be non-empty")
        if not repo_path:
            raise OrchestratorError("repo_path must be non-empty")
        # Cluster E1: surface idempotency conflicts at the public
        # boundary as ``OrchestratorError``. The RunLedger itself
        # raises ``IdempotencyKeyConflictError``; we translate here
        # so callers don't need to import the controller module.
        if idempotency_key:
            existing = self._ledger_find_by_key(self._run_ledger, idempotency_key)
            if existing is not None and existing.run_id != str(rid):
                raise OrchestratorError(
                    f"idempotency_key {idempotency_key!r} already maps to "
                    f"run_id {existing.run_id!r}; call resume_run on that "
                    f"run or pick a fresh key."
                )
        # Cluster E3: resume seam. If ``resume_from_run_id`` is set,
        # look up the persisted record in the (durable) ledger and
        # verify the spec matches. The phase loop below will skip
        # phases already completed by the prior run.
        resume_phase_index = 0
        resumed_from: RunRecord | None = None
        if resume_from_run_id is not None:
            resumed_from = self._run_ledger.get(resume_from_run_id)
            if resumed_from is None:
                raise OrchestratorError(
                    f"resume_from_run_id {resume_from_run_id!r} not found in ledger"
                )
            # Cluster E3: spec-drift guard. If the persisted
            # ``feature_description`` is set AND differs from the new
            # one, refuse the resume — the caller may have changed
            # the spec mid-flight and we want a fresh run, not a
            # confused mid-run.
            if (
                resumed_from.feature_description is not None
                and resumed_from.feature_description != feature_description
            ):
                raise OrchestratorError(
                    f"resume_from_run_id {resume_from_run_id!r} has "
                    f"feature_description {resumed_from.feature_description!r} "
                    f"but caller passed {feature_description!r}; spec drift "
                    f"detected. Pick a fresh run_id to start over."
                )
            # Compute the phase loop index to skip completed phases.
            # When ``resumed_from.phase`` is ``None`` (no prior phase
            # recorded — e.g. the prior run was an old pre-E3 run),
            # fall back to index 0 (start from scratch), which
            # preserves back-compat for old ledger records.
            #
            # Cluster WP1 / story WP1.2: when the structured
            # ``cursor`` is present, use it to decide whether to retry
            # the failed phase (outcome ∈ {failed, blocked, paused})
            # or skip the last completed phase (outcome ∈ {ok, skipped}).
            # This is the canonical resume policy; the legacy
            # ``resumed_from.phase`` string is the fallback for
            # pre-WP1 records.
            if resumed_from.cursor is not None:
                cur = resumed_from.cursor
                try:
                    cur_phase_idx = PHASE_SEQUENCE.index(PhaseName(cur.current_phase))
                except ValueError:
                    raise OrchestratorError(
                        f"resume_from_run_id {resume_from_run_id!r} has "
                        f"unknown cursor phase {cur.current_phase!r}; refusing "
                        f"to resume from an unknown phase."
                    ) from None
                if cur.phase_outcome in {"failed", "blocked", "paused"}:
                    # Retry the failed phase (don't advance the index).
                    resume_phase_index = cur_phase_idx
                else:
                    # Successful checkpoint — resume AFTER it.
                    last = cur.last_completed_phase or cur.current_phase
                    try:
                        resume_phase_index = PHASE_SEQUENCE.index(PhaseName(last)) + 1
                    except ValueError:
                        resume_phase_index = cur_phase_idx + 1
            elif resumed_from.phase is not None:
                try:
                    resume_phase_index = PHASE_SEQUENCE.index(PhaseName(resumed_from.phase)) + 1
                except ValueError:
                    raise OrchestratorError(
                        f"resume_from_run_id {resume_from_run_id!r} has "
                        f"unknown phase {resumed_from.phase!r}; refusing "
                        f"to resume from an unknown phase."
                    ) from None
        repo = Path(repo_path).resolve()
        run_dir = Path(self._config.execution_root) / str(rid)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Resolve trace writer (Cluster E stories E5+E6). ``"auto"``
        # creates a per-run JSONL file; ``None`` disables tracing;
        # an explicit instance is used as-is and never closed by us.
        tw_default = self._trace_writer_default
        trace_writer: TraceWriter | None
        owns_writer: bool
        if tw_default == "auto":
            trace_writer = Trace.for_run(run_id=str(rid), run_dir=run_dir)
            owns_writer = True
        elif tw_default is None:
            trace_writer = None
            owns_writer = False
        else:
            assert isinstance(tw_default, TraceWriter)
            trace_writer = tw_default
            owns_writer = False
        self._trace_writer_active = trace_writer

        ctx = RunContext(
            run_id=rid,
            feature_description=feature_description,
            repo_path=str(repo),
        )
        # Cluster E3: when resuming, rebuild the ctx from the
        # persisted snapshot so subsequent phases see the prior
        # run's accumulated state. ``to_jsonable`` was applied at
        # write time; we just need to construct a fresh
        # ``RunContext`` from the dict.
        if resumed_from is not None and resumed_from.ctx is not None:
            ctx = _ctx_from_persisted(
                run_id=rid,
                persisted=resumed_from.ctx,
                fallback_feature=feature_description,
                fallback_repo=str(repo),
            )
        # Record the run start in the shared ledger. Cluster E3:
        # ``feature_description`` is persisted on the ledger record so
        # the resume seam can verify spec-match.
        self._run_ledger.record_start(
            str(rid),
            repository=str(repo),
            idempotency_key=idempotency_key,
            feature_description=feature_description,
        )
        # E4b: register a fresh cancellation token for this run.
        # ``cancel_run`` looks it up + flips it; the runner watches it.
        cancel_token = CancellationToken()
        self._cancel_tokens[str(rid)] = cancel_token

        events: list[PipelineEvent] = []
        # SPEC §line 587 canonicalizes the terminal phrase as
        # ``"completed"`` — the controller's RunState stores the same
        # value via RunState.COMPLETE.value (renamed in Cluster A so the
        # internal enum matches the SPEC). The pipeline returns the
        # SPEC phrase; the ledger records the same phrase.
        terminal_state = PhaseName.COMPLETED.value
        # Track the set of files already present in ``run_dir`` so we
        # can emit ``artifact_produced`` events for new ones after
        # each phase (Cluster E story E5).
        seen_artifacts: set[str] = set()
        try:
            # Cluster E3: when resuming, slice the phase sequence so
            # already-completed phases are skipped (their work was
            # captured in the persisted ctx).
            phases_to_run = PHASE_SEQUENCE[resume_phase_index:]
            for phase in phases_to_run:
                spec = PhaseSpec(run_id=rid, phase=phase)
                self._emit_trace(
                    PhaseStarted(run_id=str(rid), phase=phase.value, attempt=spec.attempt)
                )
                outcome, ctx, detail = self._run_phase(spec=spec, ctx=ctx, run_dir=run_dir)
                # Cluster E3: persist the resume cursor after every
                # phase (success OR failure). A failed phase stops
                # the run; the cursor advances to the failed phase
                # so the next resume picks up from there.
                #
                # Cluster WP1 / story WP1.1: also write the
                # ``phase_outcome`` so the cursor records whether to
                # retry the phase on the next resume. The attempt
                # counter is taken from the PhaseSpec so concurrent
                # retries bump monotonically.
                self._run_ledger.record_phase(
                    str(rid),
                    phase=phase.value,
                    ctx=_ctx_to_persisted(ctx),
                    phase_outcome=outcome.value,
                    phase_attempt=spec.attempt,
                )
                events.append(
                    PipelineEvent(
                        phase=phase.value,
                        timestamp=time.time(),
                        detail=detail or f"{phase.value} {outcome.value}",
                    )
                )
                self._emit_trace_outcome(
                    rid=str(rid),
                    phase=phase.value,
                    outcome=outcome,
                    detail=detail,
                )
                self._emit_artifacts(
                    rid=str(rid),
                    phase=phase.value,
                    run_dir=run_dir,
                    seen=seen_artifacts,
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
            self._emit_trace(
                PhaseFailed(
                    run_id=str(rid),
                    phase="orchestrator",
                    outcome="error",
                    error=str(exc),
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
            self._emit_trace(
                PhaseFailed(
                    run_id=str(rid),
                    phase="orchestrator",
                    outcome="error",
                    error=repr(exc),
                )
            )
            terminal_state = RunState.FAILED.value

        result = PipelineResult(
            run_id=str(rid),
            terminal_state=terminal_state,
            events=tuple(events),
            # Cluster WP1 / story WP1.4: surface the final RunContext
            # so callers can read profile_path / specification_path /
            # plan_id / review_verdict / pr_url / ci_outcome without
            # querying the ledger. ``ctx`` is the dataclass instance
            # mutated by each phase handler as it advances.
            context=ctx,
        )
        self._events[str(rid)] = result.events
        # E4b: deregister the cancellation token now that the run is
        # terminal. ``cancel_run`` after this point still works (it
        # marks the ledger) but cannot interrupt an already-finished
        # subprocess — which is fine because the subprocess is gone.
        self._cancel_tokens.pop(str(rid), None)
        if owns_writer and trace_writer is not None:
            trace_writer.close()
            self._trace_writer_active = None
        return result

    def resume_run(self, run_id: str) -> PipelineResult:
        """Resume a paused/failed run from the last successful phase.

        Cluster E3: uses the new ``start_run(resume_from_run_id=...)``
        seam so the run continues from where it stopped (using the
        persisted ``phase`` + ``ctx`` from the ledger). Pre-E3, this
        method re-ran the run from scratch with a synthetic feature
        description; that path is gone now and replaced by the
        spec-match guard in ``start_run``.

        Caller behaviour:
        - ``feature_description`` is read from the persisted record
          (so the Telegram handler / CLI doesn't need to remember it).
        - If the persisted record predates E3 (``feature_description``
          is ``None``), we fall back to ``f"resume:{run_id}"`` for
          back-compat; spec-drift detection is skipped in that case.
        """
        rec = self._run_ledger.get(run_id)
        if rec is None:
            raise OrchestratorError(f"unknown run_id: {run_id}")
        if rec.state in {RunState.COMPLETE, RunState.CANCELLED}:
            raise OrchestratorError(
                f"run {run_id} is in terminal state {rec.state.value}; cannot resume"
            )
        # Mark resume in the ledger (the CAS-friendly way). This bumps
        # revision so concurrent watchers see the change.
        self._run_ledger.mark_resume(run_id)
        feature = rec.feature_description or f"resume:{run_id}"
        return self.start_run(
            feature_description=feature,
            repo_path=rec.repository,
            run_id=RunId(run_id),
            resume_from_run_id=run_id,
        )

    def cancel_run(self, run_id: str) -> None:
        """Cancel a run.

        E4b: this does two things in order:

        1. **Flip the per-run cancellation token** (if the run is
           still in-flight and the token is registered). The runner
           watches the token and escalates to SIGTERM/SIGKILL on
           the running subprocess. The ``start_run`` call that is
           blocked in the runner will return promptly with
           ``exit_code=130`` ("cancelled by orchestrator").
        2. **Mark the ledger as CANCELLED.** This was the entire
           behaviour before E4b; kept for backward compatibility
           and for the case where ``start_run`` has already
           finished (no token registered) but the caller still
           wants the ledger to reflect a cancellation.

        Idempotent: calling ``cancel_run`` twice is safe. The
        second call will not raise even if the run is already
        CANCELLED in the ledger (the token, if still registered,
        is a no-op when set twice).
        """
        rec = self._run_ledger.get(run_id)
        if rec is None:
            raise OrchestratorError(f"unknown run_id: {run_id}")
        if rec.state == RunState.COMPLETE:
            raise OrchestratorError(f"run {run_id} already complete; cannot cancel")
        # E4b: flip the per-run token (if registered). This wakes
        # the runner's watcher, which sends SIGTERM, waits the
        # grace window, then SIGKILL.
        token = self._cancel_tokens.get(run_id)
        if token is not None:
            token.set()
        self._run_ledger.mark_cancelled(run_id)

    # ----- E4b runner helpers ------------------------------------------

    def _cancel_token_for(self, run_id: str) -> CancellationToken | None:
        """Return the per-run cancellation token (or ``None`` if absent).

        Phase handlers use this to thread cancellation into the
        runner without reaching into ``self._cancel_tokens`` directly.
        """
        return self._cancel_tokens.get(run_id)

    # ----- E1 idempotency helpers --------------------------------------

    @staticmethod
    def _ledger_find_by_key(ledger: RunLedger, key: str) -> RunRecord | None:
        """Look up a ledger record by its idempotency_key.

        Reads the ledger's internal ``_key_index`` to avoid scanning.
        Returns ``None`` if the key is empty or unset. Module-level
        so it can be unit-tested without instantiating an
        Orchestrator.
        """
        if not key:
            return None
        idx = getattr(ledger, "_key_index", None)
        if not isinstance(idx, dict):
            return None
        rid = idx.get(key)
        if rid is None:
            return None
        return ledger.get(rid)

    # ----- trace helpers (Cluster E, stories E5+E6) ----------------------

    def _emit_trace(self, event: TraceEvent) -> None:
        """Emit a single trace event to the active writer (no-op if disabled)."""
        tw = self._trace_writer_active
        if tw is None:
            return
        with contextlib.suppress(Exception):
            # Trace emission must never break the run; swallow.
            tw.emit(event)

    def _emit_trace_outcome(
        self,
        *,
        rid: str,
        phase: str,
        outcome: PhaseOutcome,
        detail: str,
    ) -> None:
        """Emit the appropriate trace event for a phase outcome."""
        if outcome in {PhaseOutcome.OK, PhaseOutcome.SKIPPED}:
            self._emit_trace(
                PhaseCompleted(
                    run_id=rid,
                    phase=phase,
                    outcome="ok" if outcome == PhaseOutcome.OK else "skipped",
                    detail=detail or "",
                )
            )
            return
        # FAILED / BLOCKED / PAUSED — emit a phase_failed event with the
        # matching outcome string.
        outcome_str: str
        if outcome == PhaseOutcome.FAILED:
            outcome_str = "failed"
        elif outcome == PhaseOutcome.BLOCKED:
            outcome_str = "blocked"
        elif outcome == PhaseOutcome.PAUSED:
            outcome_str = "paused"
        else:
            outcome_str = "failed"  # fallback
        self._emit_trace(
            PhaseFailed(
                run_id=rid,
                phase=phase,
                outcome=outcome_str,
                error=detail or "",
            )
        )

    def _emit_artifacts(
        self,
        *,
        rid: str,
        phase: str,
        run_dir: Path,
        seen: set[str],
    ) -> None:
        """Emit ``artifact_produced`` events for new files under ``run_dir``.

        Skips subdirectories beyond ``execution/`` so we don't spam
        the trace with per-task files. The ``execution/<task_id>/``
        subtree is summarised by emitting its top-level task marker
        instead.
        """
        if not run_dir.exists():
            return
        for child in run_dir.iterdir():
            name = child.name
            if name == "trace.jsonl":
                continue
            full = str(child.relative_to(run_dir))
            if full in seen:
                continue
            seen.add(full)
            if child.is_file():
                self._emit_trace(
                    ArtifactProduced(
                        run_id=rid,
                        phase=phase,
                        path=full,
                        artifact_kind=_classify_artifact(name),
                    )
                )
            elif child.is_dir() and name != "execution":
                # Treat the directory itself as one logical artefact
                # (e.g. ``execution/<task_id>/red``).
                self._emit_trace(
                    ArtifactProduced(
                        run_id=rid,
                        phase=phase,
                        path=full,
                        artifact_kind="directory",
                    )
                )

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
            # Cluster WP1 / story WP1.2: even on a fatal phase failure
            # we return ``FAILED`` instead of re-raising so the
            # outer ``start_run`` loop records the failed-phase cursor
            # (which is what makes resume retry the right phase). The
            # outer loop's ``except OrchestratorError`` catch is now a
            # backstop for truly unhandled exceptions, not the
            # primary failure-routing path.
            if info.fatal_on_failure:
                return PhaseOutcome.FAILED, ctx, f"fatal phase: {exc}"
            return PhaseOutcome.FAILED, ctx, f"phase failed: {exc}"
        except Exception as exc:
            if info.fatal_on_failure:
                # Same WP1 rationale: surface as FAILED so the cursor
                # records the failed phase. ``detail`` carries the
                # original exception text for diagnostics.
                return PhaseOutcome.FAILED, ctx, f"fatal phase: {exc!r}"
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


_ARTIFACT_KIND_BY_NAME: dict[str, str] = {
    "specification.json": "spec",
    "plan.json": "plan",
    "repo-profile.json": "profile",
    "review-verdict.json": "review",
    "result.json": "task_result",
    "diff.patch": "diff",
}


def _classify_artifact(filename: str) -> str:
    """Map an artifact filename to a stable ``artifact_kind`` string.

    Unknown names fall through to ``"file"`` so the trace still
    records them; downstream consumers can refine later.
    """
    return _ARTIFACT_KIND_BY_NAME.get(filename, "file")


def _phase_feature_request(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    return PhaseOutcome.OK, ctx, "feature request accepted"


def _phase_repository_discovery(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    profile = _RepoProfiler.profile(repo_path=Path(ctx.repo_path), run_dir=run_dir)
    # Cluster WP1 / story WP1.4: surface the artifact path in the
    # context so callers can introspect the profile without
    # re-deriving it from run_dir. ``profile.name`` is the basename;
    # callers usually want the absolute path so they can ``open()``
    # it directly, hence ``run_dir / profile.name``.
    new_ctx = replace(ctx, profile_path=str(run_dir / profile.name))
    return PhaseOutcome.OK, new_ctx, f"profile written: {profile.name}"


def _phase_specification(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    # Cluster WP3 (story H): delegate to the configured
    # ``SpecificationService``. The deterministic default writes the
    # exact same JSON shape as before, so tests that introspect
    # ``specification.json`` keep passing.
    artifact = orch._services.specification.produce(ctx=ctx, run_dir=run_dir)
    spec_path = Path(run_dir) / "specification.json"
    new_ctx = replace(ctx, specification_path=str(spec_path))
    provider = artifact.provider.value if artifact.provider else "deterministic"
    return (
        PhaseOutcome.OK,
        new_ctx,
        f"specification written: {spec_path.name} (provider={provider})",
    )


def _phase_planning(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    # Cluster WP3 (story H): delegate to the configured
    # ``PlanningService``. Default composition wraps ``_PlanBuilder``
    # so existing tests keep passing.
    plan = orch._services.planning.build(ctx=ctx)
    plan_path = run_dir / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2) + "\n")
    # Cluster WP1 / story WP1.4: surface the plan id so callers can
    # correlate later artifacts (e.g. task_results) with the plan
    # that produced them.
    new_ctx = replace(ctx, plan_id=plan.plan_id)
    return PhaseOutcome.OK, new_ctx, f"plan produced: {plan.plan_id}"


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
        orch._runner.run_task(
            red_dir=r,
            green_dir=g,
            task_id=task.task_id,
            cancel=orch._cancel_token_for(str(ctx.run_id)),
        )

    try:
        result = svc.execute(plan=plan, task_id=task.task_id, runner=_runner)
    except Exception as exc:
        # Convert any TaskExecutionService failure into a PhaseOutcome.
        raise OrchestratorError(f"implementation failed: {exc}") from exc
    # Cluster WP1 / story WP1.4: surface a per-task summary in the
    # context so callers (and the dashboard) can introspect task
    # results without re-running ``svc.execute``. We append the new
    # result rather than replacing, so re-execution (e.g. on resume
    # retry) accumulates the history.
    new_task_results = (
        *ctx.task_results,
        {
            "task_id": task.task_id,
            "violations": list(result.violations),
            "summary": str(getattr(result, "summary", "")),
        },
    )
    new_ctx = replace(ctx, task_results=new_task_results)
    return (
        PhaseOutcome.OK,
        new_ctx,
        f"task {task.task_id} executed: violations={list(result.violations)}",
    )


def _phase_validation(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    task = plan.tasks[0]
    if not task.validation_commands:
        # Cluster WP1 / story WP1.4: surface the SKIPPED state in the
        # context so callers don't see ``validation_exit_code=None``
        # and conclude the phase ran but produced no exit code.
        new_ctx = replace(ctx, validation_exit_code=None)
        return PhaseOutcome.SKIPPED, new_ctx, "no validation commands"
    cmd = task.validation_commands[0]
    result = orch._runner.run_validation(
        command=cmd,
        cwd=Path(ctx.repo_path),
        timeout_s=60.0,
        cancel=orch._cancel_token_for(str(ctx.run_id)),
    )
    detail = f"{cmd} → exit {result.exit_code}"
    # Cluster WP1 / story WP1.4: surface the actual exit code on the
    # context regardless of phase outcome. The SKIPPED branch sets it
    # to ``None``; the OK and FAILED branches set it to the actual
    # exit code so downstream consumers (dashboard, failure-routing)
    # can branch on it without re-running the validation.
    new_ctx = replace(ctx, validation_exit_code=result.exit_code)
    if result.exit_code != 0:
        return PhaseOutcome.FAILED, new_ctx, detail
    return PhaseOutcome.OK, new_ctx, detail


def _phase_remediation(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    # Cluster WP3 (story H): delegate to the configured
    # ``RemediationService``. The deterministic default marks the
    # gate passed (matches the legacy behaviour) and the model-backed
    # composition classifies any prior implementation error via the
    # ``ErrorKind`` mapping in :mod:`seharness.orchestrator.services`.
    plan = _PlanBuilder.build(ctx=ctx)
    # If we have no recorded prior outcome we just acknowledge the
    # remediation gate passed — the orchestrator will not call this
    # service with a real ``prior_outcome`` until a later PR wires
    # ``ImplementationOutcome`` into the run ledger. The protocol
    # already exists; this phase handler stays simple for now.
    if plan.tasks:
        _ = orch._services.remediation.remediate(
            ctx=ctx,
            plan=plan,
            task_id=plan.tasks[0].task_id,
            prior_outcome=_ZERO_OUTCOME,
        )
    return PhaseOutcome.OK, ctx, "no outstanding violations"


def _phase_review(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    plan = _PlanBuilder.build(ctx=ctx)
    # Cluster WP3 (story H): delegate to the configured
    # ``ReviewService`` with a *fresh* context — the SPEC requires
    # review never to receive prior implementation chat history or
    # trace events. ``ReviewContext`` is the structural enforcement
    # of that rule.
    review_verdict: ReviewVerdict = orch._services.review.review(
        review_ctx=ReviewContext(
            approved_spec=_build_review_spec(ctx, run_dir),
            impact={},
            plan=plan,
            final_diff="",
            validation_results={},
            coverage_results={},
            run_dir=run_dir,
        )
    )
    legacy_verdict = _verdict_to_legacy(review_verdict)
    # Cluster WP1 / story WP1.4: surface the review verdict in the
    # context so callers / the controller can branch on it. The
    # verdict is recorded regardless of phase outcome — both OK and
    # FAILED branches carry the actual review result, never ``None``.
    new_ctx = replace(ctx, review_verdict=legacy_verdict)
    if legacy_verdict != "approve":
        return PhaseOutcome.FAILED, new_ctx, f"review verdict: {legacy_verdict}"
    return PhaseOutcome.OK, new_ctx, f"verdict: {legacy_verdict}"


def _phase_draft_pr(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    url = orch._pr_client.create(
        branch=f"agent/feature-{str(spec.run_id).replace('orch-', '')}",
        title=f"feat: {ctx.feature_description[:60]}",
        body=f"Automated run {spec.run_id}.",
        draft=orch._config.pr_draft,
    )
    # Cluster WP1 / story WP1.4: surface the PR URL so callers can
    # navigate to the draft without re-querying the PR client. Even
    # stub clients return a synthetic URL; we surface it verbatim.
    new_ctx = replace(ctx, pr_url=url)
    return PhaseOutcome.OK, new_ctx, f"draft PR: {url}"


def _phase_ci(
    orch: Orchestrator, *, spec: PhaseSpec, ctx: RunContext, run_dir: Path
) -> tuple[PhaseOutcome, RunContext, str]:
    monitor = orch._ci_monitor
    if monitor is None:
        # No real monitor wired; declare the run CI-ready if validation
        # passed (which it did, otherwise we'd have routed to failed).
        # Cluster WP1 / story WP1.4: surface the no-monitor condition
        # as a distinct outcome so the dashboard can flag runs that
        # passed without CI evidence (vs. runs that have actual CI).
        new_ctx = replace(ctx, ci_outcome="no_monitor")
        return PhaseOutcome.OK, new_ctx, "CI monitor not configured; assuming ready"
    # Real monitor: invoke .run() with bounded budget.
    if not hasattr(monitor, "run"):
        new_ctx = replace(ctx, ci_outcome="no_run_method")
        return PhaseOutcome.OK, new_ctx, "monitor missing run(); assuming ready"
    # We do NOT call .run() here because it blocks until the PR is
    # ready. /pr_status uses view_factory for an instant view; the
    # orchestrator mirrors that pattern by inspecting the most recent
    # view without polling.
    view_factory = getattr(monitor, "_view_factory", None)
    if view_factory is None:
        new_ctx = replace(ctx, ci_outcome="no_view_factory")
        return PhaseOutcome.OK, new_ctx, "monitor has no view_factory; assuming ready"
    view = view_factory()
    if view is None:
        new_ctx = replace(ctx, ci_outcome="no_view")
        return PhaseOutcome.OK, new_ctx, "no view available; assuming ready"
    from seharness.ci.readiness import ReadyEvaluator  # noqa: PLC0415

    decision = ReadyEvaluator().evaluate(view)
    # Cluster WP1 / story WP1.4: surface the actual decision so
    # callers learn whether the run is truly ready vs. still
    # pending. ``ci_outcome`` is the SPEC §"Phase 9" phrase.
    if not decision.can_be_ready:
        new_ctx = replace(ctx, ci_outcome="not_ready")
        return PhaseOutcome.FAILED, new_ctx, "CI not ready"
    new_ctx = replace(ctx, ci_outcome="ready")
    return PhaseOutcome.OK, new_ctx, "CI ready"


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


# ---------------------------------------------------------------------------
# Cluster WP1 / story WP1.5 — OrchestrationService Protocol
# ---------------------------------------------------------------------------
#
# The controller layer (slice 11) used to dispatch to ``Orchestrator``
# via ``isinstance(self._task_executor, Orchestrator)``, which
# hard-couples the controller to the concrete ``Orchestrator`` class.
# ``OrchestrationService`` is the structural interface the controller
# (and any future wiring layer) depends on. ``Orchestrator``
# implements it implicitly; test doubles can implement it explicitly.
#
# Methods mirror ``Orchestrator.start_run`` / ``.resume_run`` /
# ``.cancel_run``. The Protocol is intentionally narrow — adding a
# method here is a deliberate API change that callers must support.


class OrchestrationService(Protocol):
    """Structural interface to the orchestrator.

    Implemented by :class:`Orchestrator`; test doubles may implement
    the same interface for stubbed dispatch in the controller. The
    controller accepts any conformer; production deployments must
    use :class:`Orchestrator` so the WP2 fail-closed validator
    catches accidentally-wired stubs.

    Cluster WP1 / story WP1.5.
    """

    def start_run(  # mirror Orchestrator signature
        self,
        *,
        feature_description: str,
        repo_path: str,
        run_id: RunId | None = None,
        idempotency_key: str = "",
        resume_from_run_id: str | None = None,
    ) -> PipelineResult: ...

    def resume_run(self, run_id: str) -> PipelineResult: ...

    def cancel_run(self, run_id: str) -> None: ...


__all__ = [
    "OrchestrationService",
    "Orchestrator",
    "OrchestratorError",
    "PipelineEvent",
    "PipelineResult",
]


# ---------------------------------------------------------------------------
# Cluster E3: ctx <-> persisted dict helpers
# ---------------------------------------------------------------------------
#
# ``RunContext`` is a frozen dataclass with JSON-friendly fields
# (strs, ints, tuples of dicts, datetime). We use ``dataclasses.asdict``
# on the way in (with the datetime coerced to ISO format) and the
# ``RunContext`` constructor on the way out. ``to_jsonable`` handles
# any nested Pydantic models the phase handlers stuffed into ctx.


def _ctx_to_persisted(ctx: RunContext) -> dict[str, Any]:
    """Serialise a ``RunContext`` to a JSON-friendly dict.

    Cluster E3: called after each phase to capture the resume
    cursor. The ``to_jsonable`` helper coerces any Pydantic models
    phase handlers stored in ``ctx.task_results`` or elsewhere.
    """
    raw = asdict(ctx)
    # asdict serialises ``datetime`` to a vanilla ``datetime`` object
    # (not a string). Coerce to ISO format so ``json.dumps`` is happy.
    started = raw.get("started_at")
    if started is not None and hasattr(started, "isoformat"):
        raw["started_at"] = started.isoformat()
    result: dict[str, Any] = to_jsonable(raw)
    return result


def _ctx_from_persisted(
    *,
    run_id: RunId,
    persisted: dict[str, Any],
    fallback_feature: str,
    fallback_repo: str,
) -> RunContext:
    """Rebuild a ``RunContext`` from a persisted dict.

    Cluster E3: called at the start of ``start_run`` when the caller
    passes ``resume_from_run_id``. Fields missing from the persisted
    snapshot fall back to the fresh-run defaults so older E3 records
    that lack a field still load cleanly.
    """
    started_raw = persisted.get("started_at")
    if started_raw is None:
        started = datetime.now(tz=UTC)
    elif isinstance(started_raw, str):
        # fromisoformat handles the standard ``...+00:00`` form we
        # wrote on the way in. Strip trailing ``Z`` if present
        # (older Python versions don't accept it).
        normalised = started_raw.replace("Z", "+00:00")
        started = datetime.fromisoformat(normalised)
    else:
        # Backstop: a non-string value should never land here, but
        # if it does we surface it via ``now()`` rather than crash.
        started = datetime.now(tz=UTC)
    return RunContext(
        run_id=run_id,
        feature_description=persisted.get("feature_description", fallback_feature),
        repo_path=persisted.get("repo_path", fallback_repo),
        profile_path=persisted.get("profile_path"),
        specification_path=persisted.get("specification_path"),
        plan_id=persisted.get("plan_id"),
        task_results=tuple(persisted.get("task_results", ())),
        validation_exit_code=persisted.get("validation_exit_code"),
        review_verdict=persisted.get("review_verdict"),
        pr_url=persisted.get("pr_url"),
        ci_outcome=persisted.get("ci_outcome"),
        started_at=started,
    )
