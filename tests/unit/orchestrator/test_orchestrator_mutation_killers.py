"""Mutation killers for the canonical Orchestrator (Cluster A).

These tests assert structural properties that must hold even if a
mutant silently mutates a default value, swaps a comparison, or
short-circuits a branch. They also assert the auto-merge prevention
contract: the orchestrator must NEVER expose a merge method.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.controller.run_ledger import RunLedger, RunState
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig, PhaseName
from seharness.orchestrator.orchestrator import _PHASE_HANDLERS


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def x() -> str:\n    return 'y'\n")
    return repo


def _orch(tmp_path: Path) -> tuple[Orchestrator, RunLedger, Path]:
    repo = _make_repo(tmp_path)
    ledger = RunLedger()
    cfg = OrchestratorConfig(execution_root=str(tmp_path / ".runs"))
    orch = Orchestrator(run_ledger=ledger, config=cfg)
    return orch, ledger, repo


# ---------------------------------------------------------------------------
# Phase sequence integrity
# ---------------------------------------------------------------------------


def test_phase_sequence_has_exactly_twelve_entries() -> None:
    """Mutant killer: removing a phase must fail this test."""
    from seharness.orchestrator import PHASE_SEQUENCE

    assert len(PHASE_SEQUENCE) == 12


def test_phase_sequence_matches_spec_phase_eight() -> None:
    """Mutant killer: reordering phases must fail this test.

    SPEC §"Phase 8" mandates the canonical 12-phase order.
    """
    from seharness.orchestrator import PHASE_SEQUENCE

    expected = (
        PhaseName.FEATURE_REQUEST,
        PhaseName.REPOSITORY_DISCOVERY,
        PhaseName.SPECIFICATION,
        PhaseName.PLANNING,
        PhaseName.IMPLEMENTATION,
        PhaseName.VALIDATION,
        PhaseName.REMEDIATION,
        PhaseName.REVIEW,
        PhaseName.DRAFT_PR,
        PhaseName.CI,
        PhaseName.READY,
        PhaseName.COMPLETED,
    )
    assert expected == PHASE_SEQUENCE


def test_every_phase_has_a_handler() -> None:
    """Mutant killer: a missing phase handler must fail this test."""
    for phase in PhaseName:
        assert phase in _PHASE_HANDLERS, f"no handler for {phase}"


# ---------------------------------------------------------------------------
# Terminal-state mapping integrity
# ---------------------------------------------------------------------------


def test_completed_terminal_state_uses_spec_phrase(tmp_path: Path) -> None:
    """Mutant killer: terminal_state MUST equal ``"completed"``."""
    orch, _, repo = _orch(tmp_path)
    result = orch.start_run(feature_description="x", repo_path=str(repo))
    assert result.terminal_state == "completed"


def test_failed_terminal_state_uses_runstate_failed_value(tmp_path: Path) -> None:
    """Mutant killer: failed must map to RunState.FAILED.value."""
    orch, _ledger, repo = _orch(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    original = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.FAILED, kwargs["ctx"], "no"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = fail  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original  # type: ignore[assignment]
    assert result.terminal_state == RunState.FAILED.value


def test_blocked_terminal_state_routes_to_ledger_blocked(tmp_path: Path) -> None:
    orch, ledger, repo = _orch(tmp_path)
    from seharness.orchestrator import orchestrator as orch_mod
    from seharness.orchestrator.types import PhaseOutcome

    original = orch_mod._PHASE_HANDLERS[PhaseName.REVIEW]

    def block(*args, **kwargs):  # type: ignore[no-untyped-def]
        return PhaseOutcome.BLOCKED, kwargs["ctx"], "policy"

    orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = block  # type: ignore[assignment]
    try:
        result = orch.start_run(feature_description="x", repo_path=str(repo))
    finally:
        orch_mod._PHASE_HANDLERS[PhaseName.REVIEW] = original  # type: ignore[assignment]
    assert result.terminal_state == RunState.BLOCKED.value
    assert ledger.get(result.run_id).state == RunState.BLOCKED


# ---------------------------------------------------------------------------
# Auto-merge prevention (Cluster A adds to the 5-layer defense)
# ---------------------------------------------------------------------------


def test_orchestrator_has_no_merge_method() -> None:
    """Mutant killer: any merge* method on Orchestrator is forbidden."""
    forbidden = {"merge", "merge_pull_request", "auto_merge", "merge_pr", "gh_merge"}
    for name in dir(Orchestrator):
        if name.startswith("_"):
            continue
        assert name not in forbidden, f"Orchestrator exposes forbidden method: {name}"


def test_orchestrator_pr_client_uses_draft(tmp_path: Path) -> None:
    """Mutant killer: PR must be created with ``draft=True``."""
    repo = _make_repo(tmp_path)
    ledger = RunLedger()
    cfg = OrchestratorConfig(execution_root=str(tmp_path / ".runs"))
    pr = StubPullRequestClient()
    orch = Orchestrator(run_ledger=ledger, config=cfg, pr_client=pr)
    orch.start_run(feature_description="x", repo_path=str(repo))
    assert pr.created, "PR was not created"
    assert pr.created[0]["draft"] is True


def test_orchestrator_default_config_uses_draft_pr() -> None:
    """Mutant killer: default config must have ``pr_draft=True``."""
    cfg = OrchestratorConfig()
    assert cfg.pr_draft is True


def test_orchestrator_max_remediation_attempts_default_is_positive() -> None:
    """Mutant killer: must be ≥ 1 by default."""
    cfg = OrchestratorConfig()
    assert cfg.max_remediation_attempts >= 1


def test_orchestrator_max_validation_attempts_default_is_positive() -> None:
    cfg = OrchestratorConfig()
    assert cfg.max_validation_attempts >= 1


def test_orchestrator_config_rejects_invalid_max_remediation() -> None:
    """Mutant killer: validation must enforce ≥ 1."""
    with pytest.raises(ValueError):
        OrchestratorConfig(max_remediation_attempts=0)


def test_orchestrator_config_rejects_invalid_max_validation() -> None:
    with pytest.raises(ValueError):
        OrchestratorConfig(max_validation_attempts=0)
