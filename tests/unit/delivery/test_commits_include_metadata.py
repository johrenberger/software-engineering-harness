"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 3.

'Commits include requirement metadata':
- Per SPEC §'19. Git Automation' commit format:

  feat(scope): concise description

  Feature: <feature-id>
  Task: <task-id>
  Requirements: <ids>
  Scenarios: <ids>

- CommitMessage MUST validate + render this format.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.delivery.commit import CommitMessage


def _msg(**overrides: object) -> CommitMessage:
    base: dict[str, object] = {
        "scope": "auth",
        "description": "add password reset",
        "feature_id": "F-1",
        "task_id": "T-1",
        "requirement_ids": ("FR-1", "NFR-1"),
        "scenario_ids": ("SCN-1", "SCN-2"),
    }
    base.update(overrides)
    return CommitMessage(**base)  # type: ignore[arg-type]


def test_commit_message_renders_correct_format() -> None:
    msg = _msg()
    rendered = msg.render()
    assert rendered.startswith("feat(auth): add password reset\n\n")
    assert "Feature: F-1" in rendered
    assert "Task: T-1" in rendered
    assert "Requirements: FR-1, NFR-1" in rendered
    assert "Scenarios: SCN-1, SCN-2" in rendered


def test_commit_message_requires_feature_id() -> None:
    with pytest.raises(ValidationError):
        _msg(feature_id="")


def test_commit_message_requires_task_id() -> None:
    with pytest.raises(ValidationError):
        _msg(task_id="")


def test_commit_message_requires_description() -> None:
    with pytest.raises(ValidationError):
        _msg(description="")


def test_commit_message_requirement_ids_default_empty() -> None:
    msg = CommitMessage(
        scope="x",
        description="y",
        feature_id="F-1",
        task_id="T-1",
    )
    assert msg.requirement_ids == ()
    assert msg.scenario_ids == ()


def test_commit_message_empty_requirement_ids_renders_correctly() -> None:
    msg = CommitMessage(
        scope="x",
        description="y",
        feature_id="F-1",
        task_id="T-1",
    )
    rendered = msg.render()
    assert "Requirements: " in rendered or "Requirements:\n" in rendered
    assert "Scenarios: " in rendered or "Scenarios:\n" in rendered


def test_commit_message_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CommitMessage(
            scope="x",
            description="y",
            feature_id="F-1",
            task_id="T-1",
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_commit_message_is_frozen() -> None:
    msg = _msg()
    with pytest.raises(ValidationError):
        msg.scope = "other"  # type: ignore[misc]


def test_commit_message_render_is_deterministic() -> None:
    """render() MUST be deterministic — same input → same output."""
    msg1 = _msg()
    msg2 = _msg()
    assert msg1.render() == msg2.render()


def test_commit_message_handles_single_requirement() -> None:
    msg = _msg(requirement_ids=("FR-1",), scenario_ids=("SCN-1",))
    rendered = msg.render()
    assert "Requirements: FR-1" in rendered
    assert "Scenarios: SCN-1" in rendered