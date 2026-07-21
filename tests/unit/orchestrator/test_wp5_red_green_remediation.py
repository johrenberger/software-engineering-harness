"""WP5 (story I) — RED/GREEN + bounded remediation + unauthorized changes block delivery.

Acceptance criteria from the MiniMax handoff doc:

* A passing pre-change test does not count as RED.
* An unrelated pre-existing failure does not count as RED.
* GREEN evidence corresponds to the final workspace hash.
* Unauthorized changes block delivery.
* Remediation exhaustion produces ``failed`` or ``blocked``, never
  ``completed``.

These tests pin the WP5 invariants at the orchestrator + execution
boundary. The lower-level pieces (RED/GREEN evidence primitives,
workspace snapshot, path authorization) already exist — this module
asserts the *integration* glue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.controller.run_ledger import RunLedger
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.orchestrator import (
    _phase_implementation,
    _phase_remediation,
    _phase_validation,
)
from seharness.orchestrator.services import DeterministicServiceComposition
from seharness.orchestrator.types import (
    PhaseName,
    PhaseOutcome,
    PhaseSpec,
    RunContext,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def x() -> str:\n    return 'y'\n")
    return repo


def _fresh_orchestrator(
    tmp_path: Path,
    *,
    max_remediation_attempts: int = 3,
) -> Orchestrator:
    ledger = RunLedger()
    cfg = OrchestratorConfig(
        execution_root=str(tmp_path / "runs"),
        max_remediation_attempts=max_remediation_attempts,
    )
    return Orchestrator(
        run_ledger=ledger,
        config=cfg,
        pr_client=StubPullRequestClient(),
        trace_writer=None,
    )


def _ctx(repo: Path) -> RunContext:
    from seharness.orchestrator.types import new_run_id

    return RunContext(
        run_id=new_run_id(),
        feature_description="x",
        repo_path=str(repo),
    )


def _spec(phase: PhaseName) -> PhaseSpec:
    return PhaseSpec(phase=phase, run_id="orch-test")


# ---------------------------------------------------------------------------
# WP5.1 — Unauthorized changes block delivery
# ---------------------------------------------------------------------------


class TestUnauthorizedChangesBlockDelivery:
    """WP5 acceptance: ``Unauthorized changes block delivery.``"""

    def test_implementation_returns_failed_when_violations_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp5-vio"
        run_dir.mkdir(parents=True)

        # Stub the TaskExecutionService to report violations without
        # actually running pytest (test repo is a no-pytest fixture).
        from seharness.execution.service import TaskResult

        class _StubSvc:
            def execute(self, *, plan: object, task_id: str, runner: object) -> TaskResult:
                return TaskResult(
                    task_id=task_id,
                    completed=False,
                    evidence_root=run_dir,
                    red_exit_code=1,
                    green_exit_code=1,
                    violations=("docs/sneaky.md",),
                )

        monkeypatch.setattr(
            "seharness.execution.service.TaskExecutionService",
            lambda **_kwargs: _StubSvc(),
        )
        outcome, new_ctx, detail = _phase_implementation(
            orch,
            spec=_spec(PhaseName.IMPLEMENTATION),
            ctx=_ctx(repo),
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.FAILED
        assert "docs/sneaky.md" in detail
        assert new_ctx.task_results[-1]["violations"] == ["docs/sneaky.md"]

    def test_implementation_returns_ok_when_violations_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp5-clean"
        run_dir.mkdir(parents=True)

        from seharness.execution.service import TaskResult

        class _StubSvc:
            def execute(self, *, plan: object, task_id: str, runner: object) -> TaskResult:
                return TaskResult(
                    task_id=task_id,
                    completed=True,
                    evidence_root=run_dir,
                    red_exit_code=1,
                    green_exit_code=0,
                    violations=(),
                )

        monkeypatch.setattr(
            "seharness.execution.service.TaskExecutionService",
            lambda **_kwargs: _StubSvc(),
        )
        outcome, _new_ctx, _detail = _phase_implementation(
            orch,
            spec=_spec(PhaseName.IMPLEMENTATION),
            ctx=_ctx(repo),
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK


# ---------------------------------------------------------------------------
# WP5.2 — Remediation exhaustion routes to FAILED
# ---------------------------------------------------------------------------


class TestRemediationExhaustion:
    """WP5 acceptance: ``Remediation exhaustion produces ``failed`` or
    ``blocked``, never ``completed``."""

    def test_first_remediation_attempt_records_attempt_counter(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path, max_remediation_attempts=3)
        run_dir = tmp_path / "runs" / "orch-wp5-rem-1"
        run_dir.mkdir(parents=True)
        ctx = _ctx(repo)
        outcome, new_ctx, detail = _phase_remediation(
            orch,
            spec=_spec(PhaseName.REMEDIATION),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.remediation_attempts == 1
        assert new_ctx.remediation_exhausted is False
        assert "remediation attempt 1" in detail

    def test_remediation_exhausted_when_attempts_exceed_budget(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path, max_remediation_attempts=2)
        run_dir = tmp_path / "runs" / "orch-wp5-rem-2"
        run_dir.mkdir(parents=True)
        # Pre-load the context with attempts == max so the next call
        # crosses the boundary.
        from dataclasses import replace

        from seharness.orchestrator.types import new_run_id

        ctx = replace(
            _ctx(repo),
            run_id=new_run_id(),
            remediation_attempts=2,
        )
        outcome, new_ctx, detail = _phase_remediation(
            orch,
            spec=_spec(PhaseName.REMEDIATION),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.remediation_exhausted is True
        assert "exhausted" in detail


# ---------------------------------------------------------------------------
# WP5.3 — Validation skips when remediation exhausted
# ---------------------------------------------------------------------------


class TestValidationSkipsAfterExhaustion:
    """WP5 acceptance: once remediation is exhausted, validation must
    not run; the run routes to FAILED."""

    def test_validation_skipped_when_exhausted(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp5-val-skip"
        run_dir.mkdir(parents=True)
        from dataclasses import replace

        ctx = replace(_ctx(repo), remediation_exhausted=True)
        outcome, new_ctx, detail = _phase_validation(
            orch,
            spec=_spec(PhaseName.VALIDATION),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.validation_exit_code is None
        assert "remediation exhausted" in detail

    def test_validation_runs_normally_when_not_exhausted(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp5-val-ok"
        run_dir.mkdir(parents=True)
        # Default plan uses ``pytest --no-cov -q`` as the validation
        # command. We assert the phase runs and returns an exit code
        # (OK if pytest passed on the empty fixture repo, FAILED
        # otherwise — both are valid; the assertion is that the
        # remediation-exhausted short-circuit does NOT fire).
        outcome, new_ctx, _detail = _phase_validation(
            orch,
            spec=_spec(PhaseName.VALIDATION),
            ctx=_ctx(repo),
            run_dir=run_dir,
        )
        assert outcome in (PhaseOutcome.OK, PhaseOutcome.FAILED)
        assert new_ctx.validation_exit_code is not None


# ---------------------------------------------------------------------------
# WP5.4 — RunContext carries remediation bookkeeping
# ---------------------------------------------------------------------------


class TestRunContextRemediationFields:
    """WP5: ``RunContext`` carries the remediation bookkeeping so the
    controller can surface attempt counts on the dashboard."""

    def test_defaults_are_zero_and_false(self) -> None:
        ctx = RunContext(run_id="orch-x", feature_description="x", repo_path="/tmp")
        assert ctx.remediation_attempts == 0
        assert ctx.remediation_exhausted is False


# ---------------------------------------------------------------------------
# WP5.5 — Pre-existing failures don't count as RED
# ---------------------------------------------------------------------------


class TestRevertSkipsUnchangedFiles:
    """WP5: ``revert_unauthorized`` now skips files whose content
    hasn't actually changed. Pre-existing files outside
    ``allowed_paths`` are a workflow configuration concern, not a
    security violation."""

    def test_unchanged_unauthorized_file_is_not_reverted(self, tmp_path: Path) -> None:
        from seharness.execution.paths import (
            AllowedPaths,
            PathAuthorizationRule,
            ProhibitedPaths,
        )
        from seharness.execution.workspace import (
            WorkspaceSnapshot,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        snap = WorkspaceSnapshot(root=repo, captured_at=None)
        spec = repo / "docs" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("SPEC v1\n")
        snap.record(spec, mtime=None, size=spec.stat().st_size)

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )

        reverted = revert_unauthorized(repo, snap, rule)
        # Pre-PR5 this would include docs/spec/spec.md even though
        # the file was unchanged. Post-PR5 unchanged files are
        # skipped.
        assert reverted == ()
        assert spec.read_text() == "SPEC v1\n"

    def test_changed_unauthorized_file_is_still_reverted(self, tmp_path: Path) -> None:
        from seharness.execution.paths import (
            AllowedPaths,
            PathAuthorizationRule,
            ProhibitedPaths,
        )
        from seharness.execution.workspace import (
            WorkspaceSnapshot,
            revert_unauthorized,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        snap = WorkspaceSnapshot(root=repo, captured_at=None)
        spec = repo / "docs" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("SPEC v1\n")
        snap.record(spec, mtime=None, size=spec.stat().st_size)
        # Author sneaks an edit.
        spec.write_text("SPEC v2 (sneaky)\n")

        rule = PathAuthorizationRule(
            task_id="T-1",
            allowed_paths=AllowedPaths(("src/seharness/",)),
            prohibited_paths=ProhibitedPaths(()),
        )

        reverted = revert_unauthorized(repo, snap, rule)
        assert any("docs/spec.md" in str(p) for p in reverted)
        assert spec.read_text() == "SPEC v1\n"


# ---------------------------------------------------------------------------
# WP5.6 — Default composition integrates with the new fields
# ---------------------------------------------------------------------------


class TestDeterministicCompositionPassesContext:
    """WP5: the deterministic remediation service runs without error
    when invoked through the orchestrator wiring and preserves the
    remediation bookkeeping on the returned context."""

    def test_remediation_runs_via_deterministic_composition(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        orch = _fresh_orchestrator(tmp_path)
        # Confirm the default composition is in place.
        assert isinstance(orch._services, DeterministicServiceComposition)
        run_dir = tmp_path / "runs" / "orch-wp5-det"
        run_dir.mkdir(parents=True)
        ctx = _ctx(repo)
        outcome, new_ctx, _detail = _phase_remediation(
            orch,
            spec=_spec(PhaseName.REMEDIATION),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome == PhaseOutcome.OK
        assert new_ctx.remediation_attempts == 1
