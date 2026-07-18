"""RED tests for slice-13 end-to-end vertical slice (SPEC §"Phase 8").

The fixture workflow runs:

    feature request
    repository discovery
    specification
    planning
    implementation
    validation
    seeded remediation
    review
    draft PR
    CI pass
    ready for review
    completed

This test runs the pipeline on a synthetic minimal FastAPI repo
generated in-test.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _import_pipeline() -> object:
    from seharness.pipeline.vertical_slice import VerticalSlicePipeline

    return VerticalSlicePipeline


def _build_fixture(tmp_path: Path) -> Path:
    """Generate a minimal FastAPI repo with one test."""
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n"
        "@app.get('/')\nasync def root():\n    return {'ok': True}\n"
    )
    (repo / "test_main.py").write_text(
        "from fastapi.testclient import TestClient\n"
        "from main import app\n\n"
        "def test_root() -> None:\n    c = TestClient(app)\n"
        "    assert c.get('/').json() == {'ok': True}\n"
    )
    (repo / "requirements.txt").write_text("fastapi\n")
    return repo


def test_pipeline_runs_to_completed(tmp_path: Path) -> None:
    cls = _import_pipeline()
    repo = _build_fixture(tmp_path)
    pipeline = cls(repo_path=repo)
    result = pipeline.run()
    assert result.terminal_state == "completed"


def test_pipeline_emits_all_phase_events(tmp_path: Path) -> None:
    cls = _import_pipeline()
    repo = _build_fixture(tmp_path)
    pipeline = cls(repo_path=repo)
    result = pipeline.run()
    phases = tuple(e.phase for e in result.events)
    required = (
        "feature_request",
        "repository_discovery",
        "specification",
        "planning",
        "implementation",
        "validation",
        "remediation",
        "review",
        "draft_pr",
        "ci",
        "ready",
        "completed",
    )
    for r in required:
        assert r in phases, f"missing phase: {r}"


def test_pipeline_terminal_state_immutable(tmp_path: Path) -> None:
    cls = _import_pipeline()
    repo = _build_fixture(tmp_path)
    pipeline = cls(repo_path=repo)
    result = pipeline.run()
    # Cannot transition out of "completed" (slice 2 invariant).
    with pytest.raises((ValueError, RuntimeError, AttributeError)):
        pipeline.transition(result.run_id, target="running")


def test_pipeline_runs_under_90_seconds(tmp_path: Path) -> None:
    """Full vertical slice must complete in <90s on a synthetic repo."""
    import time

    cls = _import_pipeline()
    repo = _build_fixture(tmp_path)
    pipeline = cls(repo_path=repo)
    start = time.monotonic()
    result = pipeline.run()
    elapsed = time.monotonic() - start
    assert elapsed < 90.0, f"pipeline took {elapsed:.1f}s"
    assert result.terminal_state == "completed"
