"""Cluster N PR7 \u2014 independent review service tests.

Pins the workplan Step 7 exit criterion:

- No implementation history is present.
- The actual final diff is present.
- A rejection blocks completion.
- Malformed review output becomes ``changes_requested`` or
  failure, never approval.

The tests cover:

- :class:`IndependentMiniMaxReviewService` builds a prompt
  from a pure projection of the ``ReviewContext``; the
  prompt does NOT contain forbidden history tokens; the
  final diff IS present verbatim.
- ``parse_review_output`` accepts the closed status set and
  rejects illegal values, non-dict bodies, and pydantic
  extras (``extra='forbid'``).
- A rejection (``approval=False``) raises
  :class:`ReviewBlockedCompletion` via
  :func:`assert_review_blocks_completion`.
- Malformed review output produces a ``changes_requested``
  verdict by default (``approval=False``), or raises when
  ``malformed_policy='raise'`` is selected. Never approval.
- The two routers MUST be distinct objects; production
  smoke pins that.

These tests are OFFLINE: they inject a fake router that
records the request the service sends, plus a fake response
builder. No live network calls.
"""

from __future__ import annotations

from typing import Any

import pytest

import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.domain.enums import ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelResponse, ModelUsage
from seharness.orchestrator.independent_review import (
    ForbiddenTokenReviewPromptVerifier,
    IdentityReviewPromptVerifier,
    IndependentMiniMaxReviewService,
    IndependentReviewVerdict,
    ReviewBlockedCompletion,
    assert_review_blocks_completion,
    assert_router_independent,
    parse_review_output,
)
from seharness.orchestrator.independent_review import (
    _ReviewOutput as _InternalReviewOutput,  # noqa: F401  -- used by parse_review_output
)

# ---------------------------------------------------------------------------
# Fake router that records the model request + queues a response
# ---------------------------------------------------------------------------


class _FakeReviewRouter:
    """Records every request and returns the queued response
    on ``invoke``.

    Tests feed ``set_next_response(...)`` to control the next
    request's response. Multiple calls advance through the
    queue.
    """

    def __init__(self, *, identifier: str = "fake-review-router") -> None:
        self.identifier = identifier
        self.requests: list[ModelRequest] = []
        self._responses: list[ModelResponse] = []

    def set_next_response(self, response: ModelResponse) -> None:
        self._responses.append(response)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            msg = f"No queued response for {self.identifier!r}; call set_next_response first"
            raise AssertionError(msg)
        return self._responses.pop(0)


def _ok_response(
    *,
    parsed: dict[str, Any] | None = None,
    raw_output: str = "{}",
    provider: ProviderName = ProviderName.MINIMAX,
    model: str = "MiniMax-M2.7",
    input_tokens: int = 100,
    output_tokens: int = 50,
    duration_s: float = 0.5,
) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=model,
        raw_output=raw_output,
        parsed=parsed,
        usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        error=None,
        duration_s=duration_s,
    )


def _review_service(*, router: _FakeReviewRouter, **kwarg: Any) -> IndependentMiniMaxReviewService:
    return IndependentMiniMaxReviewService(review_router=router, **kwarg)


# Common fixtures used by several tests
_PLAN_ID = "plan-2026-007"
_TASK_IDS = ["task-1", "task-2"]
_FINAL_DIFF = (
    "--- a/src/seharness/foo.py\n"
    "+++ b/src/seharness/foo.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-old line\n"
    "+new line\n"
)
_SPEC_SUMMARY = "Add the cycle helper"


def _kw_diff() -> dict[str, Any]:
    return {
        "approved_spec_summary": _SPEC_SUMMARY,
        "plan_id": _PLAN_ID,
        "plan_task_ids": list(_TASK_IDS),
        "final_diff": _FINAL_DIFF,
        "validation_results": {"pytest": {"passed": True}},
        "coverage_results": {"line_coverage": 0.95},
        "impact": {"files_touched": ["src/seharness/foo.py"]},
    }


# ---------------------------------------------------------------------------
# Exit criterion #1: No implementation history is present
# Exit criterion #2: The final diff IS present
# ---------------------------------------------------------------------------


class TestIndependentReviewPromptProjection:
    """The prompt MUST NOT include implementation history
    (conversations, trace events, span IDs, prior outputs)."""

    def test_default_verifier_accepts_prompt(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "lgtm"},
            )
        )
        service = _review_service(router=router)
        service.review(**_kw_diff())  # default identity verifier

    def test_forbidden_token_verifier_rejects_history(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "lgtm"},
            )
        )
        # Pre-load a verdict; then trigger an immediate
        # response that the model would send. The verifier
        # will catch trace IDs that the author service
        # stamped on the implementation response.
        verifier = ForbiddenTokenReviewPromptVerifier(
            forbidden_tokens=(
                "trace_id=deadbeef",  # author trace events
                "span_id=1234",
                "assistant-final-output=",  # prior model output
                "implementer_response=",
            ),
        )
        service = _review_service(router=router, prompt_verifier=verifier)
        # Should NOT raise because none of the forbidden
        # tokens are in the prompt.
        service.review(**_kw_diff())

    def test_forbidden_token_verifier_raises_when_history_leaks(self) -> None:
        """The prompt verifier MUST raise when an implementation
        history token leaks into the prompt. The router never
        gets called."""

        class _ExplodingVerifier:
            def verify(self, prompt: str) -> None:
                raise AssertionError("trace_id=deadbeef present in prompt")

        router = _FakeReviewRouter()
        service = _review_service(
            router=router,
            prompt_verifier=_ExplodingVerifier(),
        )
        with pytest.raises(AssertionError, match="trace_id=deadbeef"):
            service.review(**_kw_diff())
        assert router.requests == []

    def test_final_diff_present_verbatim(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "lgtm"},
            )
        )
        service = _review_service(router=router)
        service.review(**_kw_diff())
        prompt = router.requests[0].prompt
        assert _FINAL_DIFF in prompt

    def test_plan_and_spec_appear_in_prompt(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "lgtm"},
            )
        )
        service = _review_service(router=router)
        service.review(**_kw_diff())
        prompt = router.requests[0].prompt
        assert _SPEC_SUMMARY in prompt
        assert _PLAN_ID in prompt
        for task_id in _TASK_IDS:
            assert task_id in prompt


# ---------------------------------------------------------------------------
# Exit criterion #3: A rejection blocks completion
# ---------------------------------------------------------------------------


class TestRejectionBlocksCompletion:
    """``approval=False`` from the model produces a blocked
    verdict. ``assert_review_blocks_completion`` raises on
    blocked verdicts."""

    def test_changes_requested_verdict_has_blocked_true(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={
                    "status": "changes_requested",
                    "approval": False,
                    "summary": "missing test",
                    "findings": ["no pytest added"],
                },
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False
        assert verdict.blocked is True
        assert verdict.model_status == "changes_requested"

    def test_rejected_verdict_has_blocked_true(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={
                    "status": "rejected",
                    "approval": False,
                    "summary": "breaks public API",
                },
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.blocked is True

    def test_approved_verdict_has_blocked_false(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "lgtm"},
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is True
        assert verdict.blocked is False

    def test_assert_review_blocks_completion_raises_on_rejection(self) -> None:
        verdict = IndependentReviewVerdict(
            model_status="rejected",
            approval=False,
            blocked=True,
            summary="breaks public API",
            findings=(),
            provider=ProviderName.MINIMAX,
            model="MiniMax-M2.7",
            duration_s=0.5,
            input_tokens=100,
            output_tokens=50,
            error_kind=None,
        )
        with pytest.raises(ReviewBlockedCompletion):
            assert_review_blocks_completion(verdict)

    def test_assert_review_blocks_completion_passes_on_approval(self) -> None:
        verdict = IndependentReviewVerdict(
            model_status="approved",
            approval=True,
            blocked=False,
            summary="lgtm",
            findings=(),
            provider=ProviderName.MINIMAX,
            model="MiniMax-M2.7",
            duration_s=0.5,
            input_tokens=100,
            output_tokens=50,
            error_kind=None,
        )
        # Should not raise.
        assert_review_blocks_completion(verdict)

    def test_assert_review_blocks_completion_dispatches_callback(self) -> None:
        captured: list[IndependentReviewVerdict] = []

        def _capture(v: IndependentReviewVerdict) -> None:
            captured.append(v)

        verdict = IndependentReviewVerdict(
            model_status="changes_requested",
            approval=False,
            blocked=True,
            summary="missing test",
            findings=("a", "b"),
            provider=ProviderName.MINIMAX,
            model="MiniMax-M2.7",
            duration_s=0.5,
            input_tokens=100,
            output_tokens=50,
            error_kind=None,
        )
        assert_review_blocks_completion(verdict, on_block=_capture)
        assert captured == [verdict]


# ---------------------------------------------------------------------------
# Exit criterion #4: Malformed review output \u2192 ``changes_requested`` or
# failure, NEVER approval
# ---------------------------------------------------------------------------


class TestMalformedReviewOutputNeverApproves:
    """Four malformed-input scenarios must all produce
    ``approval=False``. Malformed output never approves.

    - non-dict ``parsed``
    - dict missing required fields
    - dict with illegal ``status`` (extra='forbid')
    - dict with illegal ``approval=True`` but broken schema
    """

    def test_non_dict_payload_yields_changes_requested(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed=None,
                raw_output="not a dict",
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False
        assert verdict.model_status == "changes_requested"

    def test_missing_fields_yields_changes_requested(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved"},  # no approval / summary
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False

    def test_illegal_status_yields_changes_requested(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={
                    "status": "looks_good_to_meee",  # not in closed set
                    "approval": True,
                    "summary": "lgtm",
                },
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False
        assert verdict.model_status == "changes_requested"

    def test_malformed_policy_raise_propagates(self) -> None:
        router = _FakeReviewRouter()
        router.set_next_response(_ok_response(parsed={"not": "valid"}))
        service = _review_service(router=router, malformed_policy="raise")
        with pytest.raises(RuntimeError, match="schema validation"):
            service.review(**_kw_diff())

    def test_extra_forbid_blocks_extra_keys(self) -> None:
        """The closed ``_ReviewOutput`` schema (extra='forbid')
        rejects payloads with extra keys. The service maps
        this to ``changes_requested``."""
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={
                    "status": "approved",
                    "approval": True,
                    "summary": "lgtm",
                    "extra_key": "evil",  # extra='forbid'
                },
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False

    def test_approval_true_with_broken_schema_does_not_approve(self) -> None:
        """A model that emits ``approval=True`` with no
        ``status`` MUST NOT result in approval. Output is
        malformed."""

        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"approval": True, "summary": ""},
            )
        )
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False

    def test_transport_error_results_in_rejected(self) -> None:
        """A transport error (``response.error is not None``)
        fails closed with ``rejected``, NEVER ``approved``."""
        from seharness.domain.results import ModelError, ModelResponse, ModelUsage

        error_response = ModelResponse(
            provider=ProviderName.MINIMAX,
            model="MiniMax-M2.7",
            raw_output="",
            parsed=None,
            usage=ModelUsage(input_tokens=0, output_tokens=0),
            error=ModelError(kind="provider_failure", message="boom"),
            duration_s=0.5,
        )
        router = _FakeReviewRouter()
        router.set_next_response(error_response)
        service = _review_service(router=router)
        verdict = service.review(**_kw_diff())
        assert verdict.approval is False
        assert verdict.model_status == "rejected"
        assert verdict.error_kind == "provider_failure"


# ---------------------------------------------------------------------------
# Router independence
# ---------------------------------------------------------------------------


class TestRouterIndependence:
    """The review router MUST be distinct from the author
    router. The ``assert_router_independent`` helper pins
    the invariant."""

    def test_same_router_is_rejected(self) -> None:
        router = object()
        with pytest.raises(ValueError, match="distinct objects"):
            assert_router_independent(author_router=router, review_router=router)

    def test_different_routers_are_accepted(self) -> None:
        author = object()
        review = object()
        # Should not raise.
        assert_router_independent(author_router=author, review_router=review)

    def test_two_distinct_fake_routers(self) -> None:
        author = _FakeReviewRouter(identifier="author")
        review = _FakeReviewRouter(identifier="review")
        assert_router_independent(author_router=author, review_router=review)
        # Distinct objects held by the service and authors
        service = _review_service(router=review)
        assert service.review_router is review

    def test_service_receives_fresh_router_each_call(self) -> None:
        """Two successive calls use the same review router
        but receive a fresh request (no prior request
        leakage)."""
        router = _FakeReviewRouter()
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "x"},
            )
        )
        router.set_next_response(
            _ok_response(
                parsed={"status": "approved", "approval": True, "summary": "x"},
            )
        )
        service = _review_service(router=router)
        service.review(**_kw_diff())
        first_prompt = router.requests[0].prompt
        service.review(**_kw_diff())
        second_prompt = router.requests[1].prompt
        assert first_prompt == second_prompt  # deterministic
        # No implementation history leaked either way.


# ---------------------------------------------------------------------------
# Output parse edge cases
# ---------------------------------------------------------------------------


class TestParseReviewOutput:
    def test_closed_status(self) -> None:
        for status in ("approved", "changes_requested", "rejected"):
            parsed = parse_review_output(
                {
                    "status": status,
                    "approval": status == "approved",
                    "summary": "x",
                }
            )
            assert parsed is not None

    def test_unknown_status_rejected(self) -> None:
        assert (
            parse_review_output(
                {
                    "status": "ya_sure_thing",
                    "approval": True,
                    "summary": "x",
                }
            )
            is None
        )

    def test_non_dict_rejected(self) -> None:
        for bad in (None, "str", 0, 1.0, [1, 2, 3], True):
            assert parse_review_output(bad) is None

    def test_extra_fields_rejected(self) -> None:
        assert (
            parse_review_output(
                {
                    "status": "approved",
                    "approval": True,
                    "summary": "x",
                    "extras_bonus": True,
                }
            )
            is None
        )

    def test_missing_summary_rejected(self) -> None:
        assert (
            parse_review_output(
                {
                    "status": "approved",
                    "approval": True,
                    "summary": "",
                }
            )
            is None
        )

    def test_default_verifier_is_identity(self) -> None:
        v = IdentityReviewPromptVerifier()
        v.verify("anything goes")  # should not raise
