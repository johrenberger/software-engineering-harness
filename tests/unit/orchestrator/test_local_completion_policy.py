"""Cluster M3-3: tests for LocalCompletionPolicy + DRAFT_PR/CI skip.

The corrective doc §"Local completion" requires remote PR and
CI phases to be explicitly marked when the run is in
local-acceptance mode:

> "Remote PR and CI phases must be explicitly marked:
>
> ```text
> skipped_by_local_m3_acceptance_policy
> ```"

These tests pin the policy object and the orchestrator's
phase-handler integration with it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

# Pre-import to break the orchestrator's package init cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.orchestrator.completion_policy import (
    LOCAL_ACCEPTANCE_SKIPPED_PHASES,
    SKIP_REASON_LOCAL_M3_ACCEPTANCE,
    LocalCompletionPolicy,
)
from seharness.orchestrator.types import PhaseName, RunContext, RunId

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestSkipReasonLiteral:
    """The literal skip reason is the corrective doc's
    verbatim string. Pinning it here means a typo in any
    layer of the system fails this test first.
    """

    def test_skip_reason_is_doc_literal(self) -> None:
        assert SKIP_REASON_LOCAL_M3_ACCEPTANCE == "skipped_by_local_m3_acceptance_policy"

    def test_skipped_phases_are_pr_and_ci(self) -> None:
        assert PhaseName.DRAFT_PR in LOCAL_ACCEPTANCE_SKIPPED_PHASES
        assert PhaseName.CI in LOCAL_ACCEPTANCE_SKIPPED_PHASES
        # Other phases must NOT be in the skip list — the doc
        # only short-circuits remote-touching phases.
        for phase in LOCAL_ACCEPTANCE_SKIPPED_PHASES:
            assert phase in (PhaseName.DRAFT_PR, PhaseName.CI)


# ---------------------------------------------------------------------------
# LocalCompletionPolicy
# ---------------------------------------------------------------------------


class TestLocalCompletionPolicyDefault:
    """Empty policy (cluster-N default) means remote phases run."""

    def test_default_policy_does_not_skip(self) -> None:
        policy = LocalCompletionPolicy()
        assert policy.is_local_only is False
        assert policy.should_skip(PhaseName.DRAFT_PR) is False
        assert policy.should_skip(PhaseName.CI) is False

    def test_other_phases_never_skipped(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason="test")
        for phase in PhaseName:
            if phase in LOCAL_ACCEPTANCE_SKIPPED_PHASES:
                continue
            assert policy.should_skip(phase) is False


class TestLocalCompletionPolicyActive:
    """Active policy short-circuits remote phases only."""

    def test_active_policy_is_local_only(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE)
        assert policy.is_local_only is True

    def test_active_policy_skips_draft_pr(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE)
        assert policy.should_skip(PhaseName.DRAFT_PR) is True

    def test_active_policy_skips_ci(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE)
        assert policy.should_skip(PhaseName.CI) is True

    def test_active_policy_keeps_local_phases(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE)
        assert policy.should_skip(PhaseName.SPECIFICATION) is False
        assert policy.should_skip(PhaseName.PLANNING) is False
        assert policy.should_skip(PhaseName.REVIEW) is False


class TestLocalCompletionPolicyFrozen:
    """Frozen dataclass: mutations raise ``FrozenInstanceError``."""

    def test_frozen_raises_on_mutation(self) -> None:
        policy = LocalCompletionPolicy(remote_phases_skip_reason="x")
        with pytest.raises(FrozenInstanceError):
            policy.remote_phases_skip_reason = "y"


# ---------------------------------------------------------------------------
# RunContext M3-3 fields
# ---------------------------------------------------------------------------


def _make_run_context(**overrides: object) -> RunContext:
    defaults: dict[str, object] = {
        "run_id": RunId("run-001"),
        "feature_description": "test",
        "repo_path": "/tmp",
        "composition_id": None,
        "remote_skipped_reason": None,
    }
    defaults.update(overrides)
    return RunContext(**defaults)  # type: ignore[arg-type]


def test_composition_id_field_default_none() -> None:
    ctx = _make_run_context()
    assert ctx.composition_id is None


def test_remote_skipped_reason_field_default_none() -> None:
    ctx = _make_run_context()
    assert ctx.remote_skipped_reason is None


def test_composition_id_set() -> None:
    ctx = _make_run_context(composition_id="ModelBackedServiceComposition")
    assert ctx.composition_id == "ModelBackedServiceComposition"


def test_remote_skipped_reason_set() -> None:
    ctx = _make_run_context(remote_skipped_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE)
    assert ctx.remote_skipped_reason == SKIP_REASON_LOCAL_M3_ACCEPTANCE


def test_artifact_hash_fields_default_none() -> None:
    ctx = _make_run_context()
    assert ctx.specification_hash is None
    assert ctx.plan_hash is None
    assert ctx.test_patch_hash is None
    assert ctx.production_patch_hash is None
    assert ctx.remediation_patch_hash is None
    assert ctx.final_diff_hash is None
    assert ctx.review_verdict_hash is None
    assert ctx.profile_hash is None
    assert ctx.red_evidence_path is None
    assert ctx.green_evidence_path is None
    assert ctx.base_git_sha is None


def test_artifact_hash_fields_settable() -> None:
    ctx = _make_run_context(
        specification_hash="abc123",
        plan_hash="def456",
        review_verdict_hash="ghi789",
        base_git_sha="deadbeef00000000000000000000000000000000",
    )
    assert ctx.specification_hash == "abc123"
    assert ctx.plan_hash == "def456"
    assert ctx.review_verdict_hash == "ghi789"
    assert ctx.base_git_sha == "deadbeef00000000000000000000000000000000"
