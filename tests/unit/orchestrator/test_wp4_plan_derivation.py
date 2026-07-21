"""Tests for WP4 / PR3 — plan derivation from discovered profile.

The handoff doc (WP4 acceptance criteria) requires:

* Planning does not hard-code Python / pytest / FR-1 / SCN-1 /
  one task.
* Generated plans reference discovered commands and applicable
  repository instructions.
* Fixtures cover Python, TypeScript, and one monorepo.

These tests exercise the orchestrator's wiring end-to-end:
``_phase_repository_discovery`` produces a ``RepositoryProfile``
JSON, ``_PlanBuilder.build`` reads it back, and the resulting
``Plan`` carries the discovered ``allowed_paths`` and
``validation_commands``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# Ordering fix: import controller modules first to break the
# pre-existing orchestrator↔controller circular-import trap.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.orchestrator.orchestrator import (
    _phase_planning,
    _phase_repository_discovery,
    _RepoProfiler,
)
from seharness.orchestrator.types import (
    OrchestratorConfig,
    PhaseName,
    PhaseSpec,
    RunContext,
    RunId,
)
from seharness.repository.discovery import (
    PackageManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_git(path: Path) -> None:
    """Make sure ``path`` is a git repo with at least one commit so
    the inspector's git-state detector can populate ``base_commit``.
    """
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    subprocess.run(
        ("git", "-C", str(path), "init", "--quiet", "--initial-branch=main"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(path), "config", "user.email", "test@example.com"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(path), "config", "user.name", "test"),
        check=True,
    )
    # Commit whatever is in the directory.
    subprocess.run(("git", "-C", str(path), "add", "-A"), check=True)
    subprocess.run(
        (
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ),
        check=True,
        capture_output=True,
    )


def _make_python_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "pyrepo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname="pyrepo"\nrequires-python=">=3.12"\n')
    (repo / "AGENTS.md").write_text("# Pyrepo agents\n")
    src = repo / "src" / "pyrepo"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("VERSION = '0.1.0'\n")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_init.py").write_text("def test_version() -> None:\n    assert True\n")
    _ensure_git(repo)
    return repo


def _make_typescript_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "tsrepo"
    repo.mkdir()
    (repo / "package.json").write_text('{"name": "tsrepo"}\n')
    (repo / "index.ts").write_text("export const x = 1;\n")
    _ensure_git(repo)
    return repo


def _fresh_orchestrator(tmp_path: Path) -> object:
    """Build a real Orchestrator with a stub PR client so the
    full phase sequence runs.
    """
    from seharness.delivery.pr import StubPullRequestClient
    from seharness.orchestrator import Orchestrator

    cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
    return Orchestrator(
        run_ledger=RunLedger(),
        config=cfg,
        pr_client=StubPullRequestClient(),
    )


def _ctx(tmp_path: Path, repo_path: str) -> RunContext:
    """Minimal RunContext for handler-level tests."""
    return RunContext(
        run_id=RunId("orch-wp4-pd-1"),
        feature_description="Add /health endpoint",
        repo_path=repo_path,
        profile_path="",
        specification_path="",
        plan_id="",
        task_results=(),
        validation_exit_code=None,
        review_verdict=None,
        pr_url=None,
        ci_outcome=None,
    )


def _spec(phase: PhaseName) -> PhaseSpec:
    return PhaseSpec(run_id=RunId("orch-wp4-pd-1"), phase=phase, attempt=0)


# ---------------------------------------------------------------------------
# Handler-level: discovery + planning both populate the right state.
# ---------------------------------------------------------------------------


class TestRepositoryDiscoveryWritesRealProfile:
    def test_writes_repository_profile_json(self, tmp_path: Path) -> None:
        repo = _make_python_repo(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp4-pd-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        orch = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path, str(repo))
        outcome, new_ctx, _ = _phase_repository_discovery(
            orch,
            spec=_spec(PhaseName.REPOSITORY_DISCOVERY),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome.value == "ok"
        assert new_ctx.profile_path
        data = json.loads(Path(new_ctx.profile_path).read_text())
        # The artifact must be a full RepositoryProfile, not the
        # legacy 19-line stub.
        assert "path" in data
        assert "detected_language" in data
        assert "instruction_files" in data
        assert "is_monorepo" in data
        assert "git_dirty" in data
        assert "base_commit" in data

    def test_profile_records_agents_md(self, tmp_path: Path) -> None:
        repo = _make_python_repo(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp4-pd-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        orch = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path, str(repo))
        _, new_ctx, _ = _phase_repository_discovery(
            orch,
            spec=_spec(PhaseName.REPOSITORY_DISCOVERY),
            ctx=ctx,
            run_dir=run_dir,
        )
        data = json.loads(Path(new_ctx.profile_path).read_text())
        assert "AGENTS.md" in data["instruction_files"]


class TestPlanningReadsDiscoveredProfile:
    def test_planning_uses_discovered_allowed_paths(self, tmp_path: Path) -> None:
        repo = _make_python_repo(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp4-pd-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        orch = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path, str(repo))
        # Run discovery first so profile_path is populated.
        _, ctx, _ = _phase_repository_discovery(
            orch,
            spec=_spec(PhaseName.REPOSITORY_DISCOVERY),
            ctx=ctx,
            run_dir=run_dir,
        )
        # Then planning reads the profile.
        outcome, new_ctx, _ = _phase_planning(
            orch,
            spec=_spec(PhaseName.PLANNING),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome.value == "ok"
        assert new_ctx.plan_id
        plan = json.loads((run_dir / "plan.json").read_text())
        assert len(plan["tasks"]) == 1
        task = plan["tasks"][0]
        # allowed_paths is derived from the discovered source/test
        # roots + docs (no docs here) — must NOT be the legacy
        # hard-coded ``("src/", "tests/", "docs/")`` tuple when the
        # profile has more accurate data.
        allowed_paths = tuple(task["allowed_paths"])
        # The Python repo has src/pyrepo + tests/, so we expect at
        # least one of those to appear.
        assert any("src" in p for p in allowed_paths)
        assert any("tests" in p for p in allowed_paths)

    def test_planning_uses_discovered_validation_command(self, tmp_path: Path) -> None:
        """Cluster WP4 / story WP4.5: validation_commands comes
        from the CommandResolver over the discovered profile, not
        from a hard-coded ``pytest --no-cov -q`` string.
        """
        repo = _make_python_repo(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp4-pd-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        orch = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path, str(repo))
        _, ctx, _ = _phase_repository_discovery(
            orch,
            spec=_spec(PhaseName.REPOSITORY_DISCOVERY),
            ctx=ctx,
            run_dir=run_dir,
        )
        _phase_planning(
            orch,
            spec=_spec(PhaseName.PLANNING),
            ctx=ctx,
            run_dir=run_dir,
        )
        plan = json.loads((run_dir / "plan.json").read_text())
        task = plan["tasks"][0]
        # The discovered package manager is UNKNOWN (no lockfile)
        # which resolves to ``python -m pytest`` via CommandResolver.
        assert task["validation_commands"] == ["python -m pytest"]


class TestPlanBuilderFallback:
    """When no profile is on disk the planner falls back to the
    legacy hard-coded defaults so existing tests still pass."""

    def test_fallback_when_profile_missing(self, tmp_path: Path) -> None:
        orch = _fresh_orchestrator(tmp_path)
        ctx = _ctx(tmp_path, "/nonexistent/path")
        # No discovery ran, so profile_path is "" — _PlanBuilder
        # should not crash.
        run_dir = tmp_path / "runs" / "orch-wp4-fb-1"
        run_dir.mkdir(parents=True, exist_ok=True)
        outcome, new_ctx, _ = _phase_planning(
            orch,
            spec=_spec(PhaseName.PLANNING),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome.value == "ok"
        assert new_ctx.plan_id
        plan = json.loads((run_dir / "plan.json").read_text())
        # Falls back to the legacy defaults.
        assert plan["tasks"][0]["validation_commands"] == ["pytest --no-cov -q"]
        assert "src/" in plan["tasks"][0]["allowed_paths"]

    def test_fallback_when_profile_path_is_unreadable(self, tmp_path: Path) -> None:
        """Cluster WP4 / story WP4.5: _PlanBuilder should silently
        fall back when the profile JSON is corrupt or missing —
        a planner crash should never abort a run.
        """
        orch = _fresh_orchestrator(tmp_path)
        run_dir = tmp_path / "runs" / "orch-wp4-corrupt"
        run_dir.mkdir(parents=True, exist_ok=True)
        # profile_path points to a non-existent file.
        ctx = _ctx(tmp_path, str(tmp_path))
        ctx = ctx.__class__(
            run_id=ctx.run_id,
            feature_description=ctx.feature_description,
            repo_path=ctx.repo_path,
            profile_path=str(run_dir / "missing.json"),
            specification_path="",
            plan_id="",
            task_results=(),
            validation_exit_code=None,
            review_verdict=None,
            pr_url=None,
            ci_outcome=None,
        )
        outcome, _new_ctx, _ = _phase_planning(
            orch,
            spec=_spec(PhaseName.PLANNING),
            ctx=ctx,
            run_dir=run_dir,
        )
        assert outcome.value == "ok"
        # Falls back to legacy defaults.
        plan = json.loads((run_dir / "plan.json").read_text())
        assert plan["tasks"][0]["validation_commands"] == ["pytest --no-cov -q"]


# ---------------------------------------------------------------------------
# Repo fixtures match the WP4 acceptance criteria.
# ---------------------------------------------------------------------------


class TestFixturesMatchAcceptanceCriteria:
    """The handoff doc acceptance criteria explicitly names Python,
    TypeScript, and monorepo fixtures. Verify the inspector
    classifies each correctly.
    """

    def test_python_repo(self, tmp_path: Path) -> None:
        from seharness.repository.discovery import inspect_repository

        repo = _make_python_repo(tmp_path)
        profile = inspect_repository(repo)
        assert profile.detected_language == "python"
        assert profile.package_manager == PackageManager.UNKNOWN
        assert profile.is_monorepo is False

    def test_typescript_repo(self, tmp_path: Path) -> None:
        from seharness.repository.discovery import inspect_repository

        repo = _make_typescript_repo(tmp_path)
        profile = inspect_repository(repo)
        assert profile.detected_language == "typescript"
        assert profile.is_monorepo is False

    def test_monorepo_repo(self, tmp_path: Path) -> None:
        from seharness.repository.discovery import inspect_repository

        repo = _make_python_repo(tmp_path)
        # Add a second pyproject one level down.
        child = repo / "core"
        child.mkdir()
        (child / "pyproject.toml").write_text('[project]\nname="core"\n')
        profile = inspect_repository(repo)
        assert profile.is_monorepo is True


# ---------------------------------------------------------------------------
# Defensive branches in _RepoProfiler (PR3 diff-cover).
# ---------------------------------------------------------------------------


class TestRepoProfilerDefensiveBranches:
    def test_writes_empty_profile_when_repo_path_missing(self, tmp_path: Path) -> None:
        """Cluster WP4: when the repo path doesn't exist,
        ``_RepoProfiler.profile`` still writes a valid (empty)
        RepositoryProfile so downstream phases don't crash on JSON
        load. The artifact contains the path verbatim, marked as
        ``unknown`` language.
        """
        run_dir = tmp_path / "runs" / "orch-wp4-missing"
        run_dir.mkdir(parents=True)
        out = _RepoProfiler.profile(
            repo_path=tmp_path / "does-not-exist",
            run_dir=run_dir,
        )
        data = json.loads(out.read_text())
        assert data["detected_language"] == "unknown"
        assert data["is_monorepo"] is False
        assert data["source_roots"] == []
