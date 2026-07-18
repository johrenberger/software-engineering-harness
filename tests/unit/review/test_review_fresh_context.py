"""Tests for SPEC §'Slice 8: Independent review' RED bullet 1.

'Reviewer receives fresh context':
- The Reviewer invocation MUST NOT receive prior implementation conversation
  history.
- It MUST receive the approved spec, impact, plan, final diff, validation
  results, and coverage results — but never chat history, model scratchpad,
  or task-execution trace events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from seharness.domain.requirements import (
    FunctionalRequirement,
    NonFunctionalRequirement,
    Scenario,
)
from seharness.review.reviewer import (
    Reviewer,
    StaticReviewer,
)


def _make_approved_spec() -> dict[str, Any]:
    return {
        "requirements": (
            FunctionalRequirement(
                id="FR-1",
                summary="Reset password",
                acceptance=("User submits email", "New password emailed"),
            ),
            NonFunctionalRequirement(
                id="NFR-1",
                summary="Hash with argon2id",
                acceptance=("Hash cost ≥ 64 MiB",),
            ),
        ),
        "scenarios": (
            Scenario(
                id="SCN-1",
                summary="Forgot password flow",
                given=("User on login page",),
                when=("Clicks forgot password",),
                then=("Receives reset email",),
            ),
        ),
        "traceability": (("FR-1", ("SCN-1",)), ("NFR-1", ())),
    }


def _make_impact() -> dict[str, Any]:
    return {"files_changed": 5, "lines_added": 120, "lines_removed": 12}


def _make_plan() -> dict[str, Any]:
    return {"tasks": ("T-1", "T-2"), "ordering": ("T-1", "T-2")}


def _make_diff() -> str:
    return (
        "diff --git a/src/auth.py b/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@ -1,3 +1,4 @@\n"
        "+from argon2 import PasswordHasher\n"
    )


def _make_validation_results() -> dict[str, Any]:
    return {
        "gates": (
            {"id": "ruff-format", "status": "passed"},
            {"id": "mypy-strict", "status": "passed"},
            {"id": "pytest", "status": "passed"},
        )
    }


def _make_coverage_results() -> dict[str, Any]:
    return {"covered_requirements": ("FR-1",), "uncovered_requirements": ()}


def _make_chat_history() -> tuple[dict[str, Any], ...]:
    return (
        {
            "role": "user",
            "content": "I think we should use bcrypt instead of argon2id",
        },
        {
            "role": "assistant",
            "content": "Sure, let me switch to bcrypt. Bcrypt has wider adoption.",
        },
    )


def _make_trace_events() -> tuple[dict[str, Any], ...]:
    return (
        {"phase": "execution", "event": "task_started", "task_id": "T-1"},
        {"phase": "execution", "event": "task_failed", "task_id": "T-1"},
        {
            "phase": "execution",
            "event": "remediation_requested",
            "task_id": "T-1",
        },
    )


def test_reviewer_input_contains_required_artifacts() -> None:
    """Review input MUST contain: approved spec, impact, plan, diff, validation, coverage."""
    captured: list[dict[str, Any]] = []

    class _CaptureReviewer(StaticReviewer):
        def review(self, ctx: Any) -> Any:  # type: ignore[override]
            captured.append(ctx)
            return []

    reviewer = _CaptureReviewer()
    reviewer.review(
        type(
            "_Ctx",
            (),
            {
                "approved_spec": _make_approved_spec(),
                "impact": _make_impact(),
                "plan": _make_plan(),
                "final_diff": _make_diff(),
                "validation_results": _make_validation_results(),
                "coverage_results": _make_coverage_results(),
                "prior_chat_history": _make_chat_history(),
                "execution_trace_events": _make_trace_events(),
            },
        )()
    )
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
        assert field in ctx, f"missing required field: {field}"


def test_reviewer_input_does_not_contain_chat_history() -> None:
    """Reviewer MUST NOT receive implementation chat history."""
    captured: list[dict[str, Any]] = []

    class _CaptureReviewer(StaticReviewer):
        def review(self, ctx: Any) -> Any:  # type: ignore[override]
            captured.append(ctx)
            return []

    reviewer = _CaptureReviewer()
    reviewer.review(
        type(
            "_Ctx",
            (),
            {
                "approved_spec": _make_approved_spec(),
                "impact": _make_impact(),
                "plan": _make_plan(),
                "final_diff": _make_diff(),
                "validation_results": _make_validation_results(),
                "coverage_results": _make_coverage_results(),
                "prior_chat_history": _make_chat_history(),
                "execution_trace_events": _make_trace_events(),
            },
        )()
    )
    ctx = captured[0]
    forbidden = ("prior_chat_history", "chat_history", "conversation_history")
    for key in forbidden:
        assert key not in ctx, (
            f"reviewer received forbidden chat-history field: {key}"
        )


def test_reviewer_input_does_not_contain_execution_trace() -> None:
    """Reviewer MUST NOT receive task-execution trace events."""
    captured: list[dict[str, Any]] = []

    class _CaptureReviewer(StaticReviewer):
        def review(self, ctx: Any) -> Any:  # type: ignore[override]
            captured.append(ctx)
            return []

    reviewer = _CaptureReviewer()
    reviewer.review(
        type(
            "_Ctx",
            (),
            {
                "approved_spec": _make_approved_spec(),
                "impact": _make_impact(),
                "plan": _make_plan(),
                "final_diff": _make_diff(),
                "validation_results": _make_validation_results(),
                "coverage_results": _make_coverage_results(),
                "prior_chat_history": _make_chat_history(),
                "execution_trace_events": _make_trace_events(),
            },
        )()
    )
    ctx = captured[0]
    forbidden = (
        "execution_trace_events",
        "task_events",
        "retry_history",
        "remediation_log",
    )
    for key in forbidden:
        assert key not in ctx, (
            f"reviewer received forbidden execution-trace field: {key}"
        )


def test_reviewer_invoked_as_fresh_invocation() -> None:
    """Each review() call MUST be a fresh invocation (no internal state carried over)."""
    reviewer = StaticReviewer()

    ctx_a = type(
        "_CtxA",
        (),
        {
            "approved_spec": _make_approved_spec(),
            "impact": _make_impact(),
            "plan": _make_plan(),
            "final_diff": _make_diff(),
            "validation_results": _make_validation_results(),
            "coverage_results": _make_coverage_results(),
        },
    )()
    findings_a = list(reviewer.review(ctx_a))
    assert findings_a == []

    ctx_b = type(
        "_CtxB",
        (),
        {
            "approved_spec": _make_approved_spec(),
            "impact": _make_impact(),
            "plan": _make_plan(),
            "final_diff": _make_diff(),
            "validation_results": _make_validation_results(),
            "coverage_results": {
                "covered_requirements": (),
                "uncovered_requirements": ("FR-1",),
            },
        },
    )()
    findings_b = list(reviewer.review(ctx_b))
    assert findings_a == [], "first review state leaked into second call"


def test_reviewer_protocol_has_review_method() -> None:
    """Reviewer protocol MUST define review(ctx)."""
    assert hasattr(Reviewer, "review")