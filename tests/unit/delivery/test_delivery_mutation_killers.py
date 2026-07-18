"""Pydantic config killers for slice-9 delivery models.

Forces models to reject unknown fields and reject mutation of frozen
attributes — closing the loopholes mutmut exploits for `extra="allow"`
and `frozen=False` configs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.delivery.commit import (
    AuthorizedFileSet,
    CommitMessage,
)
from seharness.delivery.idempotency import (
    IdempotencyRecord,
)


def test_commit_message_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CommitMessage(
            scope="x",
            description="y",
            feature_id="F-1",
            task_id="T-1",
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_commit_message_rejects_empty_scope() -> None:
    with pytest.raises(ValidationError):
        CommitMessage(
            scope="",
            description="y",
            feature_id="F-1",
            task_id="T-1",
        )


def test_commit_message_is_frozen() -> None:
    msg = CommitMessage(
        scope="x",
        description="y",
        feature_id="F-1",
        task_id="T-1",
    )
    with pytest.raises(ValidationError):
        msg.scope = "other"  # type: ignore[misc]


def test_authorized_file_set_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AuthorizedFileSet(
            allowed_paths=("a",),
            prohibited_paths=(),
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_authorized_file_set_is_frozen() -> None:
    fs = AuthorizedFileSet(allowed_paths=("a",), prohibited_paths=())
    with pytest.raises(ValidationError):
        fs.allowed_paths = ("b",)  # type: ignore[misc]


def test_idempotency_record_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        IdempotencyRecord(
            commit_sha="abc",
            branch="x",
            pr_url=None,
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_idempotency_record_is_frozen() -> None:
    r = IdempotencyRecord(commit_sha="abc", branch="x", pr_url=None)
    with pytest.raises(ValidationError):
        r.commit_sha = "def"  # type: ignore[misc]


def test_commit_message_requirement_ids_default_empty() -> None:
    msg = CommitMessage(scope="x", description="y", feature_id="F-1", task_id="T-1")
    assert msg.requirement_ids == ()


def test_commit_message_scenario_ids_default_empty() -> None:
    msg = CommitMessage(scope="x", description="y", feature_id="F-1", task_id="T-1")
    assert msg.scenario_ids == ()


def test_authorized_file_set_default_prohibited_empty() -> None:
    fs = AuthorizedFileSet(allowed_paths=("a",))
    assert fs.prohibited_paths == ()


def test_authorized_file_set_overlap_rejected() -> None:
    """Per slice 5/6/7: allowed_paths and prohibited_paths MUST NOT overlap."""
    with pytest.raises(Exception):  # noqa: B017
        AuthorizedFileSet(allowed_paths=("a",), prohibited_paths=("a",))


def test_idempotency_record_rejects_empty_commit_sha() -> None:
    with pytest.raises(ValidationError):
        IdempotencyRecord(commit_sha="", branch="x", pr_url=None)
