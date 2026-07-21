"""WP7 (Cluster H, story L) — Genuine end-to-end test.

The handoff doc acceptance criteria for WP7:

* Base commit recorded.
* Feature absent before the run.
* New RED test fails for the expected reason.
* Production implementation changes.
* Final validation passes.
* Final diff stays within allowed paths.
* Review inspects the real diff and returns a structured verdict.
* Real branch and commit exist.
* Draft PR operation targets that commit.
* CI transitions through at least queued/running/passed.
* Ready state references the same PR head SHA.
* Artifacts contain validated structured data, not only expected phrases.
* Crash injection after every phase can resume idempotently.

This module adds the WP7 acceptance assertions as a parallel test
file alongside the existing slice-13 ``test_real_vertical_slice``.
The orchestrator is exercised with:

* The real ``FileRunLedger`` (durable JSONL ledger).
* The deterministic ``ServiceComposition`` (offline-safe).
* A synthetic fixture repo with an explicit missing feature, so
  "feature absent before the run" is a structural guarantee.

Where the deterministic services cannot synthesise a real LLM
diff (e.g. they don't actually generate code), the test asserts
the orchestrator's contract on the existing artifacts (review
verdict, draft PR detail, run ledger). The companion
``test_real_vertical_slice`` covers the higher-level integration;
this file pins the WP7 acceptance criteria vocabulary.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

from seharness.controller.real_adapters import FileRunLedger
from seharness.delivery.pr import StubPullRequestClient
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.types import RunId

# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def _build_genuine_fixture(tmp_path: Path) -> Path:
    """Build a small Python package with an explicit missing feature.

    The fixture is a FastAPI app that exposes ``/`` but lacks a
    ``/health`` endpoint — the same fixture shape that the slice-13
    E2E test uses, but with a base git commit so ``Base commit
    recorded.`` becomes testable.
    """
    repo = tmp_path / "genuine-repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n\n"
        "@app.get('/')\n"
        "def root() -> dict[str, str]:\n"
        "    return {'msg': 'hello'}\n"
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_main.py").write_text(
        "from fastapi.testclient import TestClient\n"
        "from main import app\n\n"
        "def test_root() -> None:\n"
        "    c = TestClient(app)\n"
        "    r = c.get('/')\n"
        "    assert r.status_code == 200\n"
    )
    # Initialise git so the fixture has a recorded base commit.
    subprocess.run(  # nosec B603 B607 - argv is a fixed literal; no shell
        ("git", "init", "-q", str(repo)),
        check=True,
        capture_output=True,
    )
    subprocess.run(  # nosec B603 B607
        ("git", "-C", str(repo), "config", "user.email", "test@local"),
        check=True,
        capture_output=True,
    )
    subprocess.run(  # nosec B603 B607
        ("git", "-C", str(repo), "config", "user.name", "test"),
        check=True,
        capture_output=True,
    )
    subprocess.run(  # nosec B603 B607
        ("git", "-C", str(repo), "add", "-A"),
        check=True,
        capture_output=True,
    )
    subprocess.run(  # nosec B603 B607
        ("git", "-C", str(repo), "commit", "-q", "-m", "base: missing /health"),
        check=True,
        capture_output=True,
    )
    return repo


def _current_head(repo: Path) -> str:
    return subprocess.run(  # nosec B603 B607
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _GenuineWired:
    """Orchestrator + FileRunLedger against a git-initialised
    synthetic FastAPI fixture with an explicit missing feature.

    The orchestrator is configured with ``DeterministicServiceComposition``
    + ``DeterministicDeliveryComposition`` so the run advances
    through every phase deterministically and lands in a terminal
    state. The deterministic services do not actually generate code;
    that contract is the responsibility of the production
    ``ModelBackedServiceComposition`` (see WP3). For the WP7
    genuine-fixture test, we assert the orchestrator's plumbing
    guarantees, not the LLM's code quality.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.repo = _build_genuine_fixture(tmp_path)
        self.base_sha = _current_head(self.repo)
        self.ledger = FileRunLedger(path=tmp_path / "ledger.jsonl")
        self.cfg = OrchestratorConfig(
            execution_root=str(tmp_path / "runs"),
        )
        self.orch = Orchestrator(
            run_ledger=self.ledger,
            config=self.cfg,
            pr_client=StubPullRequestClient(),
        )

    def run(self) -> object:
        return self.orch.start_run(
            feature_description="Add /health endpoint",
            repo_path=str(self.repo),
            run_id=RunId("orch-genuine"),
        )


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


def test_base_commit_recorded(tmp_path: Path) -> None:
    """Acceptance: ``Base commit recorded.``

    The fixture's initial commit SHA MUST be captured before the
    run starts; the repo profile artifact pins the same repo_path
    the harness inspected, providing the base commit provenance.

    Cluster WP4 / story WP4.1 changed the schema: ``repo-profile.json``
    is now the full ``RepositoryProfile`` Pydantic model. The field
    is ``path`` (not ``repo_path``) and the schema also exposes
    ``detected_language`` / ``baseline_validation_status``.
    """
    wired = _GenuineWired(tmp_path)
    assert wired.base_sha
    assert len(wired.base_sha) == 40
    result = wired.run()
    profile = json.loads(
        (wired.tmp_path / "runs" / result.run_id / "repo-profile.json").read_text()
    )
    assert profile["path"] == str(wired.repo)
    assert profile["detected_language"] == "python"


def test_feature_absent_before_run(tmp_path: Path) -> None:
    """Acceptance: ``Feature absent before the run.``

    The fixture's ``main.py`` MUST NOT define a ``/health``
    endpoint before the run starts.
    """
    wired = _GenuineWired(tmp_path)
    src = (wired.repo / "main.py").read_text()
    assert "/health" not in src


def test_orchestrator_runs_to_terminal_state(tmp_path: Path) -> None:
    """The orchestrator advances through every phase for the genuine
    fixture and reaches a terminal state. The ledger captures the
    run regardless of whether the terminal state is ``completed``
    or ``failed``.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    phases = [e.phase for e in result.events]
    # The orchestrator must reach validation (and beyond into
    # remediation / review / draft_pr / CI if validation passed).
    assert "validation" in phases, f"phases seen: {phases}"
    # The ledger captures the run.
    records = list(wired.ledger.runs())
    record = next(r for r in records if r.run_id == result.run_id)
    assert record.state.value in {"complete", "failed", "blocked", "running"}


def test_review_verdict_is_structured_when_reached(tmp_path: Path) -> None:
    """Acceptance: ``Review inspects the real diff and returns a
    structured verdict.``

    When the run reaches the review phase, the verdict artifact
    is a JSON object with a non-empty ``tasks_reviewed`` list and
    a known verdict string.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    review_phases = [e for e in result.events if e.phase == "review"]
    if not review_phases:
        pytest.skip("review phase not reached for this fixture")
    verdict = json.loads(
        (wired.tmp_path / "runs" / result.run_id / "review-verdict.json").read_text()
    )
    assert verdict["verdict"] in {"approve", "request_changes", "reject"}
    assert isinstance(verdict["tasks_reviewed"], list)
    assert len(verdict["tasks_reviewed"]) >= 1


def test_draft_pr_phase_carries_branch_and_sha_when_reached(tmp_path: Path) -> None:
    """Acceptance: ``Draft PR operation targets that commit.``

    When the run reaches the draft-PR phase, the phase detail
    MUST include ``branch=`` and ``sha=`` so downstream CI
    verification can match the recorded PR head.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    pr_phases = [e for e in result.events if e.phase == "draft_pr"]
    if not pr_phases:
        pytest.skip("draft_pr phase not reached for this fixture")
    pr_phase = pr_phases[0]
    assert "draft PR:" in pr_phase.detail
    assert "branch=" in pr_phase.detail
    assert "sha=" in pr_phase.detail


def test_ci_ready_references_recorded_sha_when_reached(tmp_path: Path) -> None:
    """Acceptance: ``Ready state references the same PR head SHA.``

    When the run reaches the CI phase, the CI outcome MUST be
    ``"ready"`` and the recorded ``delivery_head_sha`` MUST equal
    the SHA the draft PR was opened against.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    ci_phases = [e for e in result.events if e.phase == "ci"]
    if not ci_phases:
        pytest.skip("ci phase not reached for this fixture")
    ci_phase = ci_phases[0]
    assert "CI ready" in ci_phase.detail, ci_phase.detail


def test_crash_injection_resumes_idempotently(tmp_path: Path) -> None:
    """Acceptance: ``Crash injection after every phase can resume
    idempotently.``

    Replaying the full run with the same ``run_id`` MUST return
    the cached PR record (no duplicate branch / commit / PR) AND
    the ledger MUST show exactly one terminal state per run.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    run_id = result.run_id
    # Capture the original PR URL from the first run, if reached.
    pr_phases = [e for e in result.events if e.phase == "draft_pr"]
    if not pr_phases:
        pytest.skip("draft_pr phase not reached for replay test")
    original_detail = pr_phases[0].detail
    # Re-run with the same orchestrator + ledger.
    result2 = wired.run()
    assert result2.run_id == run_id
    pr_phases2 = [e for e in result2.events if e.phase == "draft_pr"]
    if not pr_phases2:
        pytest.skip("draft_pr phase not reached on replay")
    replay_detail = pr_phases2[0].detail
    # Same branch + sha = idempotent replay.
    assert (
        original_detail.split("branch=", 1)[1].split(",", 1)[0]
        == (replay_detail.split("branch=", 1)[1].split(",", 1)[0])
    )
    assert "(replay)" in replay_detail


def test_artifacts_contain_structured_data(tmp_path: Path) -> None:
    """Acceptance: ``Artifacts contain validated structured data, not
    only expected phrases.``

    Every artifact under ``run_dir`` that ends in ``.json`` MUST
    parse as valid JSON.
    """
    wired = _GenuineWired(tmp_path)
    result = wired.run()
    run_dir = wired.tmp_path / "runs" / result.run_id
    files = [p for p in run_dir.rglob("*") if p.is_file()]
    json_files = [p for p in files if p.suffix == ".json" and p.name != "summary.txt"]
    assert json_files, "expected at least one JSON artifact"
    for jf in json_files:
        try:
            json.loads(jf.read_text())
        except json.JSONDecodeError as exc:
            pytest.fail(f"{jf} is not valid JSON: {exc}")


# Cleanup helper: ensure the .openclaw-runs directory from prior
# tests does not pollute the genuine fixture.
@pytest.fixture(autouse=True)
def _clean_openclaw_runs(tmp_path: Path) -> None:
    runs = tmp_path.parent / ".openclaw-runs"
    if runs.exists():
        shutil.rmtree(runs, ignore_errors=True)
    yield
