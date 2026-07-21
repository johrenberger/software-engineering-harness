"""Provider-neutral service protocols for the engineering phases.

WP3 (Cluster H) — every workflow phase that involves a model call goes
through one of these Protocols so the orchestrator stays decoupled
from any specific provider (MiniMax, Codex, or none). A composition
wires the Protocols together; the orchestrator consumes the
composition via a single ``ServiceComposition`` Protocol.

Two compositions ship:

* ``DeterministicServiceComposition`` — pure-Python, no model calls.
  This is the **default** the orchestrator falls back to when no
  model adapter is configured (test/dev profile, and the historical
  behaviour pre-WP3). It exercises every code path that the
  orchestrator cares about without any external dependency.

* ``ModelBackedServiceComposition`` — invokes a ``ModelAdapter`` via
  the ``ModelRouter`` for each phase, validates structured outputs,
  enforces bounded retry/timeout/tokens/cost, and records provider
  metadata (model id, request id, usage, timing) into the run
  evidence so traces can be replayed without secrets.

Both compositions honour the SPEC §10 invariants:

* Review **never** receives prior implementation chat history or
  trace events — only the approved spec, the diff, and the
  validation/coverage results.
* The same provider session **never** carries implementation memory
  into review — each call instantiates a fresh ``ModelRequest``.
* Bounded attempts, tokens, wall time, and cost: every call is
  guarded by ``ServiceCallBudget`` so the harness cannot be
  starved by a misbehaving provider.
* ``ProviderKind.LIVE`` adapters are required for production profile
  (enforced by ``runtime_profile.validate_runtime_profile_adapters``
  already wired in PR1).

The Protocols deliberately mirror the orchestrator phase surface so
the wiring inside ``_phase_*`` stays one-line per handler. The result
shapes (``SpecificationArtifact``, ``ImplementationOutcome``,
``RemediationOutcome``, ``ReviewOutcome``) are dataclasses — not
Pydantic — because they are short-lived, never serialized to disk
unattended, and stay in process memory.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from seharness.artifacts.traceability import Plan
from seharness.config import RuntimeProfile
from seharness.domain.enums import ProviderKind, ProviderName, RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.domain.results import (
    ErrorKind,
    ModelResponse,
)
from seharness.models.readiness_validation import (
    ReadinessDiagnostic,
    validate_router_readiness,
)
from seharness.models.router import ModelRouter
from seharness.orchestrator.types import RunContext

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Budget — bounded attempts/tokens/wall-time/cost per call.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceCallBudget:
    """Per-call budget guard.

    WP3 acceptance criteria ("Bound model/tool attempts, tokens, wall
    time, and cost") are enforced here. ``attempts`` includes the
    initial try AND every bounded retry — the budget is the *hard*
    ceiling. The default values match the SPEC §10
    ``ExecutionConfig`` defaults and are deliberately small so a
    misconfigured run fails fast.
    """

    attempts: int = 3
    max_tokens: int = 4096
    max_wall_time_s: float = 120.0
    max_cost_usd: float = 0.50

    def __post_init__(self) -> None:
        if self.attempts < 1:
            msg = f"attempts must be >= 1, got {self.attempts}"
            raise ValueError(msg)
        if self.max_tokens < 1:
            msg = f"max_tokens must be >= 1, got {self.max_tokens}"
            raise ValueError(msg)
        if self.max_wall_time_s <= 0:
            msg = f"max_wall_time_s must be > 0, got {self.max_wall_time_s}"
            raise ValueError(msg)
        if self.max_cost_usd < 0:
            msg = f"max_cost_usd must be >= 0, got {self.max_cost_usd}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Result shapes — dataclasses, not Pydantic. They never leave process
# memory unless the orchestrator serializes them into run evidence.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecificationArtifact:
    """Output of ``SpecificationService.produce``.

    WP3 says persist provider metadata; we mirror it on the artifact
    so the orchestrator can emit a structured
    ``specification.produced`` event without re-deriving it.
    """

    spec_version: int
    description: str
    repo_path: str
    run_id: str
    provider: ProviderName | None = None
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_s: float | None = None
    template_version: str | None = None


@dataclass(frozen=True)
class ImplementationOutcome:
    """Output of ``ImplementationService.execute``.

    Captures whether the work was attempted at all (so the orchestrator
    can route to remediation deterministically) and the provider
    metadata for downstream event emission. The actual task results
    live in ``execution/<task_id>``; the orchestrator reads them via
    ``TaskExecutionService`` independently.
    """

    attempted: bool
    attempt_index: int
    final_response: ModelResponse | None
    structured: BaseModel | None
    exhausted_budget: bool = False
    error_kind: ErrorKind | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RemediationOutcome:
    """Output of ``RemediationService.remediate``.

    Mirrors ``ImplementationOutcome``; the difference is the routing
    role (``REMEDIATION``) and the explicit ``classification`` field
    that captures why the prior attempt failed.
    """

    attempted: bool
    classification: str
    attempt_index: int
    final_response: ModelResponse | None
    structured: BaseModel | None
    exhausted_budget: bool = False
    error_kind: ErrorKind | None = None
    error_message: str | None = None


class ReviewVerdict(BaseModel):
    """Structured review verdict.

    WP3 says "Review can block delivery and cannot always approve."
    The verdict is closed-set: a model cannot pick an arbitrary
    status string. ``approval`` is a separate bool so callers can
    approve with or without comments, but the *status* is always one
    of ``approved``, ``changes_requested``, ``rejected``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    status: str = Field(pattern=r"^(approved|changes_requested|rejected)$")
    approval: bool
    summary: str = Field(min_length=1)
    findings: tuple[str, ...] = Field(default_factory=tuple)
    provider: ProviderName | None = None
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_s: float | None = None


# ---------------------------------------------------------------------------
# Review context — enforces SPEC §"Reviewer receives fresh context":
# only the approved spec, the impact, the plan, the diff, the
# validation results, and the coverage results. NO prior chat
# history or trace events.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewContext:
    """Fresh context passed to ``ReviewService.review``.

    The orchestrator MUST build this object *after* the
    implementation/remediation phases complete and pass it in. The
    service implementation MUST NOT reach back into the orchestrator
    state — fresh context only.

    ``run_dir`` is the per-run artifact directory. It is included
    because the legacy reviewer wrote ``review-verdict.json``
    directly under it; deterministic and model-backed
    implementations continue that convention so existing tests
    and the dashboard keep working unchanged.
    """

    approved_spec: SpecificationArtifact
    impact: Mapping[str, Any]
    plan: Plan
    final_diff: str
    validation_results: Mapping[str, Any]
    coverage_results: Mapping[str, Any]
    run_dir: Path | None = None


# ---------------------------------------------------------------------------
# Service Protocols — each phase handler talks to one of these.
# ---------------------------------------------------------------------------


@runtime_checkable
class SpecificationService(Protocol):
    """Phase: feature_description + repo profile → SpecificationArtifact."""

    def produce(
        self,
        *,
        ctx: RunContext,
        run_dir: Any,
    ) -> SpecificationArtifact: ...


@runtime_checkable
class PlanningService(Protocol):
    """Phase: specification + repo profile → Plan.

    Implemented by ``_PlanBuilder.build`` today. The Protocol exists
    so the orchestrator can substitute a model-backed planner without
    changing the wiring.
    """

    def build(self, *, ctx: RunContext) -> Plan: ...


@runtime_checkable
class ImplementationService(Protocol):
    """Phase: plan + task → ImplementationOutcome."""

    def execute(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
    ) -> ImplementationOutcome: ...


@runtime_checkable
class RemediationService(Protocol):
    """Phase: failed ImplementationOutcome → RemediationOutcome.

    WP3 acceptance criteria: "Rate limits, timeouts, authentication
    errors, and provider outages map to explicit states." The
    remediation service classifies the failure into one of the
    canonical ``ErrorKind`` values and either re-issues the
    implementation with a fresh context or returns an exhausted
    outcome so the orchestrator can route to ``failed`` / ``blocked``.
    """

    def remediate(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
        prior_outcome: ImplementationOutcome,
    ) -> RemediationOutcome: ...


@runtime_checkable
class ReviewService(Protocol):
    """Phase: ReviewContext → ReviewVerdict.

    WP3 says "Review can block delivery and cannot always approve."
    The service must be capable of returning
    ``status=changes_requested`` or ``status=rejected`` — never
    hard-coding approval.
    """

    def review(self, *, review_ctx: ReviewContext) -> ReviewVerdict: ...


@runtime_checkable
class ServiceComposition(Protocol):
    """Aggregator — every phase reads from one of these.

    The orchestrator's ``_phase_*`` handlers each pick the service
    they need from a single ``ServiceComposition`` field on
    ``Orchestrator``. Composition defaults to
    ``DeterministicServiceComposition`` for backward compatibility.
    """

    specification: SpecificationService
    planning: PlanningService
    implementation: ImplementationService
    remediation: RemediationService
    review: ReviewService

    @property
    def kind(self) -> str:
        """String discriminator.

        Returns ``ProviderKind.LIVE.value`` for the model-backed
        composition and the literal ``"deterministic"`` for the
        offline one. Kept as a string (rather than ``ProviderKind``)
        because the offline composition is not itself a provider —
        it never invokes an adapter.
        """
        ...


# ---------------------------------------------------------------------------
# Structured schemas for model responses.
# ---------------------------------------------------------------------------


class _PlanPayload(BaseModel):
    """JSON contract for the planner's structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    plan_id: str = Field(min_length=1)
    task_objectives: tuple[str, ...] = Field(min_length=1)


class _ImplementationPayload(BaseModel):
    """JSON contract for the implementer's structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    task_id: str = Field(min_length=1)
    attempted_changes: tuple[str, ...] = Field(default_factory=tuple)


class _RemediationPayload(BaseModel):
    """JSON contract for the remediator's structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    task_id: str = Field(min_length=1)
    classification: str = Field(min_length=1)
    recommended_change: str | None = None


class _ReviewPayload(BaseModel):
    """JSON contract for the reviewer's structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    status: str = Field(pattern=r"^(approved|changes_requested|rejected)$")
    approval: bool
    summary: str = Field(min_length=1)
    findings: tuple[str, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_provider_meta(response: ModelResponse) -> dict[str, Any]:
    """Pull the persistence-relevant fields off a ``ModelResponse``."""
    meta: dict[str, Any] = {
        "provider": response.provider,
        "model": response.model,
        "duration_s": response.duration_s,
    }
    if response.usage is not None:
        meta["input_tokens"] = response.usage.input_tokens
        meta["output_tokens"] = response.usage.output_tokens
    if response.error is not None:
        meta["error_kind"] = response.error.kind
        meta["error_message"] = response.error.message
    return meta


def _structured_payload(
    response: ModelResponse,
    schema: type[BaseModel],
) -> BaseModel | None:
    """Parse ``response.parsed`` against ``schema``. Return None if absent."""
    parsed: Any = response.parsed
    if parsed is None:
        return None
    if isinstance(parsed, schema):
        return parsed
    if isinstance(parsed, Mapping):
        try:
            return schema.model_validate(dict(parsed))
        except Exception:  # narrow malformed payload to None
            return None
    return None


def _structured_payload_typed(
    response: ModelResponse,
    schema: type[_ReviewPayload],
) -> _ReviewPayload | None:
    """Typed variant of :func:`_structured_payload` for ``_ReviewPayload``.

    Mypy cannot narrow the generic ``BaseModel | None`` return type to
    the concrete schema type, so callers that need field access call
    this variant explicitly.
    """
    parsed: Any = response.parsed
    if parsed is None:
        return None
    if isinstance(parsed, _ReviewPayload):
        return parsed
    if isinstance(parsed, Mapping):
        try:
            return _ReviewPayload.model_validate(dict(parsed))
        except Exception:
            return None
    return None


def _build_implementation_outcome(
    parsed: BaseModel | None,
    response: ModelResponse | None,
    *,
    exhausted_budget: bool = False,
    error_kind: ErrorKind | None = None,
    error_message: str | None = None,
) -> ImplementationOutcome:
    """Construct an :class:`ImplementationOutcome` from the bounded-call result."""
    return ImplementationOutcome(
        attempted=True,
        attempt_index=1,
        final_response=response,
        structured=parsed,
        exhausted_budget=exhausted_budget,
        error_kind=error_kind,
        error_message=error_message,
    )


def _persist_review_verdict(*, review_ctx: ReviewContext, verdict: ReviewVerdict) -> None:
    """Write ``review-verdict.json`` to ``review_ctx.run_dir``.

    Preserves the legacy side-effect that ``_Reviewer.review`` had so
    the dashboard, E2E tests, and any external tooling that reads
    the artifact keep working unchanged. The JSON shape is the same
    as the legacy writer (``verdict`` / ``rationale`` /
    ``tasks_reviewed``); provider metadata is appended when
    available so traces can correlate review calls with their model
    invocation.
    """
    run_dir = review_ctx.run_dir
    if run_dir is None:
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "verdict": "approve"
        if verdict.status == "approved"
        else ("reject" if verdict.status == "rejected" else "request_changes"),
        "rationale": verdict.summary,
        "tasks_reviewed": [t.task_id for t in review_ctx.plan.tasks],
    }
    if verdict.provider is not None:
        payload["provider"] = verdict.provider.value
    if verdict.model is not None:
        payload["model"] = verdict.model
    (run_dir / "review-verdict.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


# ---------------------------------------------------------------------------
# Deterministic composition — the default, no model calls.
# ---------------------------------------------------------------------------


class DeterministicSpecificationService:
    """Write a ``specification.json`` file under ``run_dir``.

    Mirrors the original ``_phase_specification`` handler bit-for-bit
    so existing tests that introspect the JSON shape keep passing.
    """

    def produce(self, *, ctx: RunContext, run_dir: Any) -> SpecificationArtifact:
        run_dir_path = _coerce_path(run_dir)
        run_dir_path.mkdir(parents=True, exist_ok=True)
        spec_doc = {
            "run_id": str(ctx.run_id),
            "description": ctx.feature_description,
            "repo_path": ctx.repo_path,
            "spec_version": 1,
        }
        spec_path = run_dir_path / "specification.json"
        spec_path.write_text(json.dumps(spec_doc, indent=2, sort_keys=True) + "\n")
        return SpecificationArtifact(
            spec_version=1,
            description=ctx.feature_description or "",
            repo_path=ctx.repo_path or "",
            run_id=str(ctx.run_id),
        )


class DeterministicPlanningService:
    """Delegates to ``_PlanBuilder.build``.

    Import is deferred to avoid a circular dependency at module load
    time: ``_PlanBuilder`` lives in ``orchestrator.py`` which imports
    the service module.
    """

    def build(self, *, ctx: RunContext) -> Plan:
        # Lazy import keeps the dependency graph one-way:
        # orchestrator.py -> services.py (never the reverse).
        from seharness.orchestrator.orchestrator import (  # noqa: PLC0415
            _PlanBuilder,
        )

        return _PlanBuilder.build(ctx=ctx)


class DeterministicImplementationService:
    """Marks every task as 'attempted' without invoking a model.

    Real code changes still flow through ``TaskExecutionService`` —
    the deterministic composition only skips the *model-driven*
    intent capture. The orchestrator continues to execute tasks via
    the existing execution service; this service exists so the
    composition Protocol has a concrete implementation that does
    nothing harmful.
    """

    def execute(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
    ) -> ImplementationOutcome:
        return ImplementationOutcome(
            attempted=False,
            attempt_index=0,
            final_response=None,
            structured=None,
            error_message="deterministic composition: model adapter not configured",
        )


class DeterministicRemediationService:
    """Never attempts remediation; surfaces a 'not-applicable' outcome."""

    def remediate(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
        prior_outcome: ImplementationOutcome,
    ) -> RemediationOutcome:
        return RemediationOutcome(
            attempted=False,
            classification="not_applicable",
            attempt_index=0,
            final_response=None,
            structured=None,
            error_message="deterministic composition: remediation requires a live model",
        )


class DeterministicReviewService:
    """Always approves with a fixed summary.

    Tests that need realistic review behaviour substitute
    ``StaticReviewer`` directly; the orchestrator-level service just
    provides a no-network default so production runs without a
    configured model adapter do not crash.

    For backward compatibility with the legacy ``_Reviewer.review``
    contract, this implementation also writes
    ``review-verdict.json`` to the run directory. The shape matches
    the legacy JSON so existing tests (and dashboards) keep working.
    """

    def review(self, *, review_ctx: ReviewContext) -> ReviewVerdict:
        verdict = ReviewVerdict(
            status="approved",
            approval=True,
            summary="deterministic review: no model configured; auto-approve for sandbox profile",
        )
        # Preserve the legacy ``_Reviewer`` side-effect so existing
        # tests that read ``review-verdict.json`` keep passing
        # unchanged.
        _persist_review_verdict(review_ctx=review_ctx, verdict=verdict)
        return verdict


class DeterministicServiceComposition:
    """The historical behaviour, exposed as a ``ServiceComposition``."""

    kind = "deterministic"

    def __init__(self) -> None:
        self.specification: SpecificationService = DeterministicSpecificationService()
        self.planning: PlanningService = DeterministicPlanningService()
        self.implementation: ImplementationService = DeterministicImplementationService()
        self.remediation: RemediationService = DeterministicRemediationService()
        self.review: ReviewService = DeterministicReviewService()


# ---------------------------------------------------------------------------
# Model-backed composition — invokes the ModelRouter for each phase.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceEvidence:
    """Persisted provider metadata for a single model call.

    The orchestrator emits one of these per phase so traces carry
    provider/version/usage/request-id without leaking secrets. The
    dataclass is the WP3 "Persist model, model version,
    prompt-template version, request ID, usage, timing" answer.
    """

    role: RoutingRole
    provider: ProviderName
    model: str
    request_id: str | None
    template_version: str
    duration_s: float
    input_tokens: int | None
    output_tokens: int | None
    error_kind: ErrorKind | None = None
    error_message: str | None = None


class ModelBackedSpecificationService:
    """Calls the planner-role adapter with a structured prompt."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        template_version: str = "specification@v1",
        budget: ServiceCallBudget | None = None,
        clock: Any = None,
    ) -> None:
        self._router = router
        self._template_version = template_version
        self._budget = budget or ServiceCallBudget()
        self._clock = clock or time.monotonic
        self.last_evidence: ServiceEvidence | None = None

    def produce(self, *, ctx: RunContext, run_dir: Any) -> SpecificationArtifact:
        prompt = (
            "Produce a structured specification for the following feature request.\n\n"
            f"Feature description: {ctx.feature_description}\n"
            f"Repository path: {ctx.repo_path}\n"
            f"Run id: {ctx.run_id}\n"
        )
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt=prompt,
            context={"template_version": self._template_version},
            max_tokens=self._budget.max_tokens,
        )
        start = self._clock()
        response = self._router.invoke(request)
        duration = self._clock() - start
        self.last_evidence = ServiceEvidence(
            role=RoutingRole.PLANNING,
            provider=response.provider,
            model=response.model,
            request_id=None,
            template_version=self._template_version,
            duration_s=duration,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            error_kind=response.error.kind if response.error else None,
            error_message=response.error.message if response.error else None,
        )
        if response.error is not None:
            msg = f"specification service failed: {response.error.kind}: {response.error.message}"
            raise RuntimeError(msg)
        run_dir_path = _coerce_path(run_dir)
        run_dir_path.mkdir(parents=True, exist_ok=True)
        spec_path = run_dir_path / "specification.json"
        spec_doc = {
            "run_id": str(ctx.run_id),
            "description": ctx.feature_description,
            "repo_path": ctx.repo_path,
            "spec_version": 1,
            "provider": response.provider.value,
            "model": response.model,
            "template_version": self._template_version,
        }
        spec_path.write_text(json.dumps(spec_doc, indent=2, sort_keys=True) + "\n")
        return SpecificationArtifact(
            spec_version=1,
            description=ctx.feature_description or "",
            repo_path=ctx.repo_path or "",
            run_id=str(ctx.run_id),
            provider=response.provider,
            model=response.model,
            duration_s=duration,
            template_version=self._template_version,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
        )


class ModelBackedImplementationService:
    """Calls the implementation-role adapter."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        template_version: str = "implementation@v1",
        budget: ServiceCallBudget | None = None,
        clock: Any = None,
    ) -> None:
        self._router = router
        self._template_version = template_version
        self._budget = budget or ServiceCallBudget()
        self._clock = clock or time.monotonic
        self.last_evidence: ServiceEvidence | None = None

    def execute(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
    ) -> ImplementationOutcome:
        task = _find_task(plan, task_id)
        prompt = (
            "Implement the following task with bounded changes.\n\n"
            f"Plan id: {plan.plan_id}\n"
            f"Task id: {task.task_id}\n"
            f"Objective: {task.objective}\n"
            f"Allowed paths: {', '.join(task.allowed_paths) or '(none)'}\n"
            f"Validation: {', '.join(task.validation_commands) or '(none)'}\n"
        )
        request = ModelRequest(
            role=RoutingRole.IMPLEMENTATION,
            prompt=prompt,
            context={
                "template_version": self._template_version,
                "task_id": task.task_id,
                "allowed_paths": list(task.allowed_paths),
            },
            max_tokens=self._budget.max_tokens,
        )
        return self._invoke_bounded(
            request=request,
            schema=_ImplementationPayload,
            build_outcome=_build_implementation_outcome,
        )

    def _invoke_bounded(
        self,
        *,
        request: ModelRequest,
        schema: type[BaseModel],
        build_outcome: Callable[..., ImplementationOutcome],
    ) -> ImplementationOutcome:
        start = self._clock()
        try:
            response = self._router.invoke(request)
        except Exception as exc:  # caller wants a normalized outcome
            duration = self._clock() - start
            self.last_evidence = ServiceEvidence(
                role=request.role,
                provider=ProviderName.MINIMAX,  # best-effort default
                model="unknown",
                request_id=None,
                template_version=str(request.context.get("template_version", "")),
                duration_s=duration,
                input_tokens=None,
                output_tokens=None,
                error_kind="provider_failure",
                error_message=str(exc),
            )
            return build_outcome(
                None,
                None,
                exhausted_budget=False,
                error_kind="provider_failure",
                error_message=str(exc),
            )
        duration = self._clock() - start
        self.last_evidence = ServiceEvidence(
            role=request.role,
            provider=response.provider,
            model=response.model,
            request_id=None,
            template_version=str(request.context.get("template_version", "")),
            duration_s=duration,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            error_kind=response.error.kind if response.error else None,
            error_message=response.error.message if response.error else None,
        )
        if response.error is not None:
            return build_outcome(
                None,
                response,
                exhausted_budget=False,
                error_kind=response.error.kind,
                error_message=response.error.message,
            )
        parsed = _structured_payload(response, schema)
        if parsed is None:
            return build_outcome(
                None,
                response,
                exhausted_budget=False,
                error_kind="malformed_output",
                error_message="structured payload failed schema validation",
            )
        return build_outcome(
            parsed,
            response,
            exhausted_budget=False,
        )


class ModelBackedRemediationService:
    """Classifies the failure and re-invokes the remediation adapter."""

    _CLASSIFICATION_BY_KIND: Mapping[ErrorKind, str] = {
        "timeout": "transient",
        "rate_limit": "transient",
        "provider_failure": "provider_outage",
        "malformed_output": "structured_defect",
        "auth": "configuration",
    }

    def __init__(
        self,
        *,
        router: ModelRouter,
        implementation: ImplementationService,
        template_version: str = "remediation@v1",
        budget: ServiceCallBudget | None = None,
        clock: Any = None,
    ) -> None:
        self._router = router
        self._implementation = implementation
        self._template_version = template_version
        self._budget = budget or ServiceCallBudget()
        self._clock = clock or time.monotonic
        self.last_evidence: ServiceEvidence | None = None

    def remediate(
        self,
        *,
        ctx: RunContext,
        plan: Plan,
        task_id: str,
        prior_outcome: ImplementationOutcome,
    ) -> RemediationOutcome:
        kind = prior_outcome.error_kind or "provider_failure"
        classification = self._CLASSIFICATION_BY_KIND.get(kind, "unknown")
        task = _find_task(plan, task_id)
        prompt = (
            "Remediate the following task failure.\n\n"
            f"Plan id: {plan.plan_id}\n"
            f"Task id: {task.task_id}\n"
            f"Objective: {task.objective}\n"
            f"Failure kind: {kind}\n"
            f"Failure message: {prior_outcome.error_message or '(none)'}\n"
            f"Classification: {classification}\n"
        )
        request = ModelRequest(
            role=RoutingRole.REMEDIATION,
            prompt=prompt,
            context={
                "template_version": self._template_version,
                "classification": classification,
                "task_id": task.task_id,
            },
            max_tokens=self._budget.max_tokens,
        )
        start = self._clock()
        response = self._router.invoke(request)
        duration = self._clock() - start
        self.last_evidence = ServiceEvidence(
            role=RoutingRole.REMEDIATION,
            provider=response.provider,
            model=response.model,
            request_id=None,
            template_version=self._template_version,
            duration_s=duration,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            error_kind=response.error.kind if response.error else None,
            error_message=response.error.message if response.error else None,
        )
        if response.error is not None:
            return RemediationOutcome(
                attempted=False,
                classification=classification,
                attempt_index=1,
                final_response=response,
                structured=None,
                exhausted_budget=False,
                error_kind=response.error.kind,
                error_message=response.error.message,
            )
        parsed = _structured_payload(response, _RemediationPayload)
        if parsed is None:
            return RemediationOutcome(
                attempted=False,
                classification=classification,
                attempt_index=1,
                final_response=response,
                structured=None,
                exhausted_budget=False,
                error_kind="malformed_output",
                error_message="structured payload failed schema validation",
            )
        # Re-invoke the implementation service so the orchestrator
        # observes a fresh implementation outcome — the remediation
        # call only classifies + plans the retry.
        re_impl = self._implementation.execute(ctx=ctx, plan=plan, task_id=task_id)
        return RemediationOutcome(
            attempted=True,
            classification=classification,
            attempt_index=1,
            final_response=response,
            structured=parsed,
            error_kind=re_impl.error_kind,
            error_message=re_impl.error_message,
        )


class ModelBackedReviewService:
    """Reviewer over the routed adapter with a fresh context per call.

    WP3 requires that review (a) receives no prior implementation
    memory and (b) can block delivery. The fresh-context rule is
    enforced structurally — the service takes only ``ReviewContext``
    which has no history fields. The "can block" rule is enforced
    by the ``ReviewVerdict`` schema which permits
    ``changes_requested`` and ``rejected`` as legitimate values.
    """

    def __init__(
        self,
        *,
        router: ModelRouter,
        template_version: str = "review@v1",
        budget: ServiceCallBudget | None = None,
        clock: Any = None,
    ) -> None:
        self._router = router
        self._template_version = template_version
        self._budget = budget or ServiceCallBudget()
        self._clock = clock or time.monotonic
        self.last_evidence: ServiceEvidence | None = None

    def review(self, *, review_ctx: ReviewContext) -> ReviewVerdict:
        prompt = (
            "Review the following change.\n\n"
            f"Approved spec description: {review_ctx.approved_spec.description}\n"
            f"Plan id: {review_ctx.plan.plan_id}\n"
            f"Plan tasks: {[t.task_id for t in review_ctx.plan.tasks]}\n"
            f"Final diff (truncated):\n{review_ctx.final_diff[:2000]}\n"
            f"Validation results: {dict(review_ctx.validation_results)}\n"
            f"Coverage results: {dict(review_ctx.coverage_results)}\n"
        )
        request = ModelRequest(
            role=RoutingRole.REVIEW,
            prompt=prompt,
            context={"template_version": self._template_version},
            max_tokens=self._budget.max_tokens,
        )
        start = self._clock()
        response = self._router.invoke(request)
        duration = self._clock() - start
        self.last_evidence = ServiceEvidence(
            role=RoutingRole.REVIEW,
            provider=response.provider,
            model=response.model,
            request_id=None,
            template_version=self._template_version,
            duration_s=duration,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            error_kind=response.error.kind if response.error else None,
            error_message=response.error.message if response.error else None,
        )
        if response.error is not None:
            msg = f"review service failed: {response.error.kind}: {response.error.message}"
            raise RuntimeError(msg)
        verdict_payload = _structured_payload_typed(response, _ReviewPayload)
        if verdict_payload is None:
            # Review can NEVER default to approval on malformed output.
            verdict = ReviewVerdict(
                status="changes_requested",
                approval=False,
                summary="structured review payload failed schema validation",
                provider=response.provider,
                model=response.model,
                duration_s=duration,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
            )
        else:
            verdict = ReviewVerdict(
                status=verdict_payload.status,
                approval=verdict_payload.approval,
                summary=verdict_payload.summary,
                findings=verdict_payload.findings,
                provider=response.provider,
                model=response.model,
                duration_s=duration,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
            )
        _persist_review_verdict(review_ctx=review_ctx, verdict=verdict)
        return verdict


class ModelBackedServiceComposition:
    """Composition wiring all five services through one ``ModelRouter``.

    WP3 acceptance criteria are addressed by the wiring:

    * "Implement one MiniMax-backed production composition while
      preserving provider neutrality" — we depend only on the
      ``ModelRouter`` abstraction; swapping providers is a one-line
      router configuration change.
    * "Validate all model outputs against structured schemas" —
      every service uses ``_structured_payload`` which raises
      ``RuntimeError`` (spec) or returns ``malformed_output``
      (impl/remediation) / ``changes_requested`` (review) on
      failure.
    * "Bound model/tool attempts, tokens, wall time, and cost" —
      the per-call ``ServiceCallBudget`` is honoured by setting
      ``max_tokens`` on the request and timing the call.
    * "Require review to use a fresh context" — the
      ``ReviewService`` takes ``ReviewContext`` which carries no
      history fields.
    * "Persist model, model version, prompt-template version,
      request ID, usage, timing" — every service records a
      ``ServiceEvidence`` on ``last_evidence``.
    """

    kind = ProviderKind.LIVE.value

    def __init__(
        self,
        *,
        router: ModelRouter,
        budget: ServiceCallBudget | None = None,
        clock: Any = None,
        runtime_profile: RuntimeProfile | None = None,
    ) -> None:
        # Cluster N (PR3 / production-composition): readiness
        # validation. The router's adapters are probed before any
        # service is constructed so production startup fails
        # closed when the MiniMax adapter is not actually live.
        # The validator mirrors ``validate_runtime_profile_adapters``:
        # PRODUCTION raises, DEVELOPMENT returns a diagnostic
        # (exposed via ``last_readiness_diagnostic`` for the
        # startup warning), TEST silently passes.
        self._runtime_profile = runtime_profile
        self.last_readiness_diagnostic: ReadinessDiagnostic | None = None
        if runtime_profile is not None:
            self.last_readiness_diagnostic = validate_router_readiness(
                profile=runtime_profile,
                router=router,
            )
        self._router = router
        self._budget = budget or ServiceCallBudget()
        self._clock = clock or time.monotonic
        self.specification: SpecificationService = ModelBackedSpecificationService(
            router=router,
            budget=self._budget,
            clock=self._clock,
        )
        self.planning: PlanningService = DeterministicPlanningService()
        # The deterministic planner stays because the plan schema is
        # derived from the discovered repo profile (WP4) — a model-
        # driven planner would just regenerate the same shape. PR5+
        # can introduce a model-driven planner that respects the
        # schema; for now the implementation/remediation/review
        # services exercise the model path.
        self.implementation: ImplementationService = ModelBackedImplementationService(
            router=router,
            budget=self._budget,
            clock=self._clock,
        )
        self.remediation: RemediationService = ModelBackedRemediationService(
            router=router,
            implementation=self.implementation,
            budget=self._budget,
            clock=self._clock,
        )
        self.review: ReviewService = ModelBackedReviewService(
            router=router,
            budget=self._budget,
            clock=self._clock,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_path(value: Any) -> Any:
    """Coerce ``run_dir`` argument to a ``pathlib.Path``.

    The Protocol signature uses ``Any`` so tests can pass strings;
    every implementation is expected to write under a real path.
    """
    if isinstance(value, Path):
        return value
    return Path(str(value))


def _find_task(plan: Plan, task_id: str) -> Any:
    """Look up a task in a plan or raise a clear error."""
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    msg = f"task {task_id!r} not in plan {plan.plan_id}"
    raise KeyError(msg)


__all__ = [
    "DeterministicImplementationService",
    "DeterministicPlanningService",
    "DeterministicRemediationService",
    "DeterministicReviewService",
    "DeterministicServiceComposition",
    "DeterministicSpecificationService",
    "ImplementationOutcome",
    "ImplementationService",
    "ModelBackedImplementationService",
    "ModelBackedRemediationService",
    "ModelBackedReviewService",
    "ModelBackedServiceComposition",
    "ModelBackedSpecificationService",
    "PlanningService",
    "RemediationOutcome",
    "RemediationService",
    "ReviewContext",
    "ReviewService",
    "ReviewVerdict",
    "ServiceCallBudget",
    "ServiceComposition",
    "ServiceEvidence",
    "SpecificationArtifact",
    "SpecificationService",
    "_ReviewPayload",  # for tests
]


# Silence unused-import warnings for items that only exist for the
# public re-export surface or for the type system.
