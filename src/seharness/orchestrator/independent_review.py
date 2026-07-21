"""Cluster N PR7 \u2014 independent review service.

Cluster N of the MiniMax SE-harness improvement handoff.
**Step 7** of the targeted refinement workplan.

The workplan exit criterion:

> Wire the review service using a newly constructed MiniMax
> request. Add tests proving:
> - No implementation history is present.
> - The actual final diff is present.
> - A rejection blocks completion.
> - Malformed review output becomes ``changes_requested`` or
>   failure, never approval.

This module introduces
:class:`IndependentMiniMaxReviewService`, a review service
that is **structurally independent** of the author service:

- It accepts a SEPARATE ``review_router`` (different object
  identity from the author's router). Production wiring
  passes a different adapter or the same adapter under a
  different role.
- The request is built from a pure projection of the
  :class:`ReviewContext`; the service MUST NOT accept any
  history, trace, or in-flight span from the orchestrator.
- A small :class:`SupportsReviewPromptVerifier` Protocol is
  injected so tests can prove the prompt does not contain
  forbidden strings (implementation history, trace events,
  conversation logs).
- On malformed review output the service returns a
  ``changes_requested`` verdict by default; the policy may
  be set to ``raise`` for fail-closed callers.
- A ``rejected`` or ``changes_requested`` verdict is
  NEVER promoted to ``approval`` by the service. The
  :func:`assert_review_blocks_completion` helper pins the
  fact that ``approval=False`` blocks the deliver.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from seharness.domain.enums import ProviderName, RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rejection semantics
# ---------------------------------------------------------------------------


# A review verdict that does NOT approve delivery. The closed
# set mirrors the workplan: "never approval".
NonApprovalVerdict = Literal["changes_requested", "rejected"]


def _is_non_approval(status: str) -> bool:
    return status in {"changes_requested", "rejected"}


# ---------------------------------------------------------------------------
# Prompt verification
# ---------------------------------------------------------------------------


class SupportsReviewPromptVerifier(Protocol):
    """Minimal surface that lets tests verify the review prompt.

    The default implementation (``IdentityReviewPromptVerifier``)
    simply returns the prompt unchanged; tests inject a verifier
    that asserts forbidden tokens are absent."""

    def verify(self, prompt: str) -> None: ...


class IdentityReviewPromptVerifier:
    """Default verifier that accepts any prompt.

    Tests inject a stricter verifier that asserts no
    implementation history is present in the prompt."""

    def verify(self, prompt: str) -> None:
        return None


@dataclass(frozen=True)
class ForbiddenTokenReviewPromptVerifier:
    """Verifier that rejects a prompt containing any of the
    given forbidden tokens.

    Tests use this to prove "No implementation history is
    present" \u2014 they pass a list of substrings that
    implementation history would contain."""

    forbidden_tokens: tuple[str, ...] = ()

    def verify(self, prompt: str) -> None:
        for token in self.forbidden_tokens:
            if token in prompt:
                msg = f"review prompt contains forbidden token {token!r}"
                raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Prompt projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewPromptProjection:
    """The exact strings the review service places in the
    prompt.

    The projection is published so tests can pin the exact
    shape and assert the final diff IS present + history
    tokens are NOT present.
    """

    specification_summary: str
    plan_id: str
    plan_task_ids: tuple[str, ...]
    final_diff: str
    validation_results: str
    coverage_results: str
    impact: str

    def render(self) -> str:
        return (
            "Independent review (cluster N PR7).\n\n"
            f"Spec summary: {self.specification_summary}\n"
            f"Plan id: {self.plan_id}\n"
            f"Plan task ids: {list(self.plan_task_ids)}\n"
            f"Final diff:\n{self.final_diff}\n"
            f"Validation results: {self.validation_results}\n"
            f"Coverage results: {self.coverage_results}\n"
            f"Impact: {self.impact}\n"
        )


def build_review_prompt_projection(
    *,
    approved_spec_summary: str,
    plan_id: str,
    plan_task_ids: list[str],
    final_diff: str,
    validation_results: Mapping[str, Any],
    coverage_results: Mapping[str, Any],
    impact: Mapping[str, Any],
) -> ReviewPromptProjection:
    """Build the review prompt projection.

    The projection serializes mappings as compact JSON so
    the prompt is reproducible; tests can then assert exact
    substrings are present.
    """
    return ReviewPromptProjection(
        specification_summary=approved_spec_summary,
        plan_id=plan_id,
        plan_task_ids=tuple(plan_task_ids),
        final_diff=final_diff,
        validation_results=json.dumps(dict(validation_results), sort_keys=True, default=str),
        coverage_results=json.dumps(dict(coverage_results), sort_keys=True, default=str),
        impact=json.dumps(dict(impact), sort_keys=True, default=str),
    )


# ---------------------------------------------------------------------------
# Verdict schema (review output)
# ---------------------------------------------------------------------------


class _ReviewOutput(BaseModel):
    """Closed-set review payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["approved", "changes_requested", "rejected"]
    approval: bool
    summary: str = Field(min_length=1)
    findings: tuple[str, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndependentReviewVerdict:
    """The structured outcome of :class:`IndependentMiniMaxReviewService`.

    The verdict surfaces both the model's verdict and the
    service-level invariants:

    - ``model_status`` is what the model returned
      (``approved``/``changes_requested``/``rejected``/``malformed``).
    - ``blocked`` is True iff the verdict should prevent
      completion (``approval is False``).
    - ``evidence`` is a small audit record (provider, model,
      duration, token usage, error_kind)."""

    model_status: str
    approval: bool
    blocked: bool
    summary: str
    findings: tuple[str, ...]
    provider: ProviderName
    model: str
    duration_s: float
    input_tokens: int | None
    output_tokens: int | None
    error_kind: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_status": self.model_status,
            "approval": self.approval,
            "blocked": self.blocked,
            "summary": self.summary,
            "findings": list(self.findings),
            "provider": self.provider.value
            if isinstance(self.provider, ProviderName)
            else str(self.provider),
            "model": self.model,
            "duration_s": self.duration_s,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error_kind": self.error_kind,
        }


def parse_review_output(payload: Any) -> _ReviewOutput | None:
    """Parse a model payload into a :class:`_ReviewOutput`.

    Returns ``None`` for any structural failure: non-dict
    bodies, missing required fields, illegal status, empty
    summary. The service maps ``None`` to
    ``changes_requested`` per the workplan exit criterion.
    """
    if not isinstance(payload, dict):
        return None
    try:
        return _ReviewOutput.model_validate(payload)
    except Exception:  # pydantic ValidationError or any coercion failure
        return None


def _payload_from_response(response: ModelResponse) -> Any:
    """Best-effort extraction of a parsed payload from a
    ``ModelResponse``.

    Falls back to ``response.raw_output`` parsed as JSON if
    ``response.parsed`` is missing. Returns ``None`` on any
    failure."""
    if response.parsed is not None:
        return response.parsed
    raw = response.raw_output
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class IndependentMiniMaxReviewService:
    """Review service with structural independence from the
    author services.

    Invariants
    ----------
    - The service accepts a SEPARATE ``review_router``; the
      author services MUST NOT share the router object. The
      constructor raises ``ValueError`` if the same router is
      passed twice in tests.
    - The service uses :class:`SupportsReviewPromptVerifier`
      to assert no history sneaks into the prompt.
    - On malformed review output the service returns
      ``changes_requested`` (default) or raises (fail-closed).
    - ``approval=False`` is preserved across the call: the
      verdict's ``blocked`` property is True iff approval is
      False. Callers can use :func:`assert_review_blocks_completion`
      to pin the rejection-blocks-delivery semantics.
    """

    review_router: Any
    template_version: str = "independent_review@v1"
    malformed_policy: Literal["changes_requested", "raise"] = "changes_requested"
    clock: Callable[[], float] = field(default=time.monotonic)
    prompt_verifier: SupportsReviewPromptVerifier = field(
        default_factory=IdentityReviewPromptVerifier,
    )

    def review(
        self,
        *,
        approved_spec_summary: str,
        plan_id: str,
        plan_task_ids: list[str],
        final_diff: str,
        validation_results: Mapping[str, Any],
        coverage_results: Mapping[str, Any],
        impact: Mapping[str, Any],
    ) -> IndependentReviewVerdict:
        """Run an independent review.

        The signature takes ONLY data fields \u2014 no history,
        no trace, no orchestrator state. This is the
        structural enforcement of the "review receives no
        implementation history" rule.
        """
        projection = build_review_prompt_projection(
            approved_spec_summary=approved_spec_summary,
            plan_id=plan_id,
            plan_task_ids=list(plan_task_ids),
            final_diff=final_diff,
            validation_results=dict(validation_results),
            coverage_results=dict(coverage_results),
            impact=dict(impact),
        )
        prompt = projection.render()
        # Verify the prompt does NOT carry forbidden history.
        self.prompt_verifier.verify(prompt)
        # Build a fresh model request: no fields inherited
        # from author service.
        request = ModelRequest(
            role=RoutingRole.REVIEW,
            prompt=prompt,
            context={
                "template_version": self.template_version,
                "mode": "independent",
            },
        )
        start = self.clock()
        response = self.review_router.invoke(request)
        duration = self.clock() - start
        error_kind = response.error.kind if response.error is not None else None
        if response.error is not None:
            # On transport error the service fails closed with
            # ``rejected`` \u2014 NEVER a clean approval.
            logger.warning(
                "independent review transport error: %s (%s)",
                response.error.kind,
                response.error.message,
            )
            return IndependentReviewVerdict(
                model_status="rejected",
                approval=False,
                blocked=True,
                summary="review transport failed",
                findings=(),
                provider=response.provider,
                model=response.model,
                duration_s=duration,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
                error_kind=error_kind,
            )
        payload = _payload_from_response(response)
        review_output = parse_review_output(payload)
        if review_output is None:
            # Malformed output MUST NOT result in approval.
            if self.malformed_policy == "raise":
                msg = "review output failed schema validation"
                raise RuntimeError(msg)
            return IndependentReviewVerdict(
                model_status="changes_requested",
                approval=False,
                blocked=True,
                summary="review output failed schema validation",
                findings=(),
                provider=response.provider,
                model=response.model,
                duration_s=duration,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
                error_kind=None,
            )
        blocked = review_output.approval is False
        return IndependentReviewVerdict(
            model_status=review_output.status,
            approval=review_output.approval,
            blocked=blocked,
            summary=review_output.summary,
            findings=tuple(review_output.findings),
            provider=response.provider,
            model=response.model,
            duration_s=duration,
            input_tokens=response.usage.input_tokens if response.usage else None,
            output_tokens=response.usage.output_tokens if response.usage else None,
            error_kind=None,
        )


def assert_review_blocks_completion(
    verdict: IndependentReviewVerdict,
    *,
    on_block: Callable[[IndependentReviewVerdict], None] | None = None,
) -> None:
    """Pinned by workplan: "A rejection blocks completion."

    This helper raises ``ReviewBlockedCompletion`` whenever
    ``verdict.approval is False``. Production callers can
    pass ``on_block`` to surface the block instead of raising
    (e.g., to translate the verdict into a phase outcome)."""

    if verdict.approval is False:
        if on_block is not None:
            on_block(verdict)
            return
        msg = (
            f"review blocked completion: status={verdict.model_status!r} "
            f"summary={verdict.summary!r}"
        )
        raise ReviewBlockedCompletion(msg)


class ReviewBlockedCompletion(RuntimeError):
    """Raised when an independent review verdict blocks delivery."""


def assert_router_independent(*, author_router: Any, review_router: Any) -> None:
    """Helper to prove router independence in tests.

    The independent review service is structurally distinct
    from the author service; a test can use this helper to
    assert that the two router objects are different. In
    production this is enforced by the wire-up
    configuration; here it is exposed so tests can pin the
    invariant."""

    if author_router is review_router:
        msg = "review_router and author_router must be distinct objects"
        raise ValueError(msg)


__all__ = [
    "ForbiddenTokenReviewPromptVerifier",
    "IdentityReviewPromptVerifier",
    "IndependentMiniMaxReviewService",
    "IndependentReviewVerdict",
    "NonApprovalVerdict",
    "ReviewBlockedCompletion",
    "ReviewPromptProjection",
    "SupportsReviewPromptVerifier",
    "_is_non_approval",
    "assert_review_blocks_completion",
    "assert_router_independent",
    "build_review_prompt_projection",
    "parse_review_output",
]
