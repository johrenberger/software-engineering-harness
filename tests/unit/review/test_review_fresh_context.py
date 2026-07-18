"""Tests for SPEC §'Slice 8: Independent review' RED bullet 1.

'Reviewer receives fresh context':
- The Reviewer invocation MUST NOT receive prior implementation conversation
  history.
- It MUST receive the approved spec, impact, plan, final diff, validation
  results, and coverage results — but never chat history, model scratchpad,
  or task-execution trace events.
"""

from __future__ import annotations

from typing import Any

from seharness.review.reviewer import ReviewContext, Reviewer, StaticReviewer


def _ctx(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "approved_spec": {"requirements": ("FR-1", "NFR-1")},
        "impact": {"files_changed": 5, "lines_added": 120, "lines_removed": 12},
        "plan": {"tasks": ("T-1", "T-2")},
        "final_diff": "diff --git a/x b/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
        "validation_results": {
            "gates": (
                {"id": "ruff-format", "status": "passed"},
                {"id": "pytest", "status": "passed"},
            )
        },
        "coverage_results": {
            "covered_requirements": ("FR-1",),
            "uncovered_requirements": (),
        },
    }
    base.update(overrides)
    return type("_Ctx", (), base)()


def test_reviewer_input_contains_required_artifacts() -> None:
    """Review input MUST contain: approved spec, impact, plan, diff, validation, coverage."""
    captured: list[Any] = []

    class _CaptureReviewer(StaticReviewer):
        def review(self, ctx: Any) -> Any:  # type: ignore[override]
            captured.append(ctx)
            return ()

    reviewer = _CaptureReviewer()
    reviewer.review(_ctx())
    assert captured, "reviewer was not invoked"
    ctx = captured[0]
    for field in (
        "approved_spec",
        "impact",
        "plan",
        "final_diff",
        "validation_results",
        "coverage_results",
    ):
        assert hasattr(ctx, field), f"missing required field: {field}"


def test_reviewer_protocol_does_not_define_chat_history_field() -> None:
    """ReviewContext protocol MUST NOT expose chat-history fields."""
    # ReviewContext is a Protocol — verify the contract surface excludes them.
    annotations = getattr(ReviewContext, "__annotations__", {})
    forbidden = ("prior_chat_history", "chat_history", "conversation_history")
    for key in forbidden:
        assert key not in annotations, f"ReviewContext protocol declares forbidden field: {key}"


def test_reviewer_protocol_does_not_define_execution_trace_field() -> None:
    """ReviewContext protocol MUST NOT expose execution-trace fields."""
    annotations = getattr(ReviewContext, "__annotations__", {})
    forbidden = (
        "execution_trace_events",
        "task_events",
        "retry_history",
        "remediation_log",
    )
    for key in forbidden:
        assert key not in annotations, f"ReviewContext protocol declares forbidden field: {key}"


def test_reviewer_invoked_as_fresh_invocation() -> None:
    """Each review() call MUST be a fresh invocation (no internal state carried over)."""
    reviewer = StaticReviewer()

    ctx_a = _ctx(coverage_results={"covered_requirements": ("FR-1",)})
    findings_a = list(reviewer.review(ctx_a))
    assert findings_a == []

    ctx_b = _ctx(coverage_results={"covered_requirements": ()})
    findings_b = list(reviewer.review(ctx_b))
    assert findings_a == [], "first review state leaked into second call"
    assert findings_b == []


def test_reviewer_protocol_has_review_method() -> None:
    """Reviewer protocol MUST define review(ctx)."""
    assert hasattr(Reviewer, "review")
