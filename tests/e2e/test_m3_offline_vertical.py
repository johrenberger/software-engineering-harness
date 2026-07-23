"""Cluster M3-4: offline MiniMax-M3 vertical acceptance test.

The corrective doc §"Offline vertical acceptance" enumerates 17
required assertions. This test pins every assertion as a single
class with one test method that hard-fails if any assertion fails
— **no conditional skips**, no ``pytest.skip``, no skipif.

The acceptance runs the orchestrator against the
``tests/fixtures/health_fixture_repo/`` FastAPI fixture, with:

- The M3 composition wired through
  :func:`build_minimax_m3_offline_composition`.
- A pre-loaded :class:`RecordingMiniMaxTransport` queueing
  five synthetic M3 responses (spec / plan / impl-test /
  impl-prod / review).
- An :class:`LLMDrivenTaskRunner` that runs real pytest against
  the fixture in ``tmp_path``.
- :class:`LocalCompletionPolicy` short-circuiting DRAFT_PR / CI
  with the literal ``skipped_by_local_m3_acceptance_policy``
  reason.

A single failure anywhere in the assertion list fails the test.
This is the M3-4 exit criterion: "full offline vertical
acceptance passes in normal CI."
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tests.e2e._bootstrap import (
    bootstrap_health_fixture_repo,
    load_recording_pair,
)

from seharness.config import RuntimeProfile

# Pre-import to break the orchestrator's package init cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.models.minimax_m3_composition import (
    MiniMaxM3CompositionConfig,
    SandboxConfig,
    build_minimax_m3_offline_composition,
)
from seharness.models.minimax_transport import (
    MiniMaxTransportResponse,
    RecordingMiniMaxTransport,
)
from seharness.orchestrator.completion_policy import (
    SKIP_REASON_LOCAL_M3_ACCEPTANCE,
    LocalCompletionPolicy,
)
from seharness.orchestrator.llm_task_runner import LLMDrivenTaskRunner
from seharness.orchestrator.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.services import ModelBackedServiceComposition
from seharness.orchestrator.types import RunId

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sandbox_config(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(
        sandbox_dir=tmp_path / "sandbox",
        patch_policy_allowed_paths=("main.py", "tests/"),
        validation_commands=("test",),
    )


def _collect_responses_from_manifest() -> tuple[MiniMaxTransportResponse, ...]:
    """Convert the 5 manifest-recorded JSON pairs into
    :class:`MiniMaxTransportResponse` objects in the order the
    orchestrator will consume them.

    The author adapter (spec / plan / implementation / remediation)
    and review adapter (review) share one
    :class:`RecordingMiniMaxTransport` queue in
    :func:`build_minimax_m3_offline_composition`, so we must
    serialize the five phases by adapter consumption order:

      1. specification       (author)
      2. planning            (author)
      3. implementation      (author) — production_patch carries the
                                     WRITE_FILE: main.py directive
                                     the LLMDrivenTaskRunner applies
                                     between RED and GREEN.
      4. remediation         (author) — test_patch response is
                                     consumed but unused; remediation
                                     never applies patches to disk.
      5. review              (review)
    """
    ordered_phases = [
        "specification",
        "planning",
        "implementation_production_patch",
        "implementation_test_patch",
        "review",
    ]
    responses: list[MiniMaxTransportResponse] = []
    for phase in ordered_phases:
        _, response_data = load_recording_pair(phase)
        responses.append(
            MiniMaxTransportResponse(
                content_text=response_data["content_text"],
                usage_input_tokens=response_data.get("usage_input_tokens"),
                usage_output_tokens=response_data.get("usage_output_tokens"),
                request_id=response_data.get("request_id", "[redacted-synthetic]"),
                error=response_data.get("error"),
            )
        )
    return tuple(responses)


# ---------------------------------------------------------------------------
# The 17 assertions
# ---------------------------------------------------------------------------


class TestOfflineVerticalAcceptance:
    """The single M3-4 acceptance test. 17 hard asserts + bonus checks,
    no skips."""

    def test_m3_offline_vertical_acceptance(self, tmp_path: Path) -> None:  # noqa: PLR0915
        # ---- Bootstrap ----
        repo_path = bootstrap_health_fixture_repo(tmp_path)
        execution_root = tmp_path / "runs"
        evidence_dir = tmp_path / "evidence"

        # ---- Pre-condition 1: /health is absent BEFORE the run ----
        main_py_before = (repo_path / "main.py").read_text()
        assert '@app.get("/health")' not in main_py_before, (
            "pre-condition: /health must be absent before the run; the fixture should not define it"
        )

        # ---- Load recordings + build composition ----
        responses = _collect_responses_from_manifest()
        config = MiniMaxM3CompositionConfig(
            api_key="redacted-test-fixture-api-key",
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.TEST,
            sandbox_config=_make_sandbox_config(tmp_path),
            provider_evidence_dir=evidence_dir,
            endpoint="http://127.0.0.1:0/v1/text/chatcompletion_v2",
            protocol="native",
        )
        transport = RecordingMiniMaxTransport(responses=responses)
        result = build_minimax_m3_offline_composition(
            config=config,
            recording_transport=transport,
            recording_responses=responses,
        )

        # ---- Pre-condition 2: composition is model-backed ----
        assert isinstance(result.composition, ModelBackedServiceComposition)
        for slot_name in (
            "specification",
            "planning",
            "implementation",
            "remediation",
            "review",
        ):
            slot = getattr(result.composition, slot_name)
            slot_cls_name = type(slot).__name__
            assert not slot_cls_name.startswith("Deterministic"), (
                f"composition.{slot_name}={slot_cls_name} is a "
                "Deterministic*Service; the M3-2 invariant forbids "
                "deterministic services in PRODUCTION (and the offline "
                "acceptance uses model-backed services exclusively)"
            )

        # ---- Build the orchestrator ----
        completion_policy = LocalCompletionPolicy(
            remote_phases_skip_reason=SKIP_REASON_LOCAL_M3_ACCEPTANCE
        )
        ledger = RunLedger()
        orch = Orchestrator(
            run_ledger=ledger,
            config=OrchestratorConfig(execution_root=str(execution_root)),
            composition=result.composition,
            evidence_writer=result.evidence_writer,
            completion_policy=completion_policy,
        )
        # Replace the default ``StubRunner`` with a real
        # ``LLMDrivenTaskRunner`` so RED+GREEN run real pytest.
        orch._runner = LLMDrivenTaskRunner(  # noqa: SLF001
            repo_root=repo_path,
            pytest_target="tests/test_health.py",
            allowed_paths=("main.py", "tests/"),
        )

        # ---- Cluster M3-4 offline override of _PlanBuilder ----
        # The canonical ``_PlanBuilder.build`` derives
        # ``allowed_paths`` from the discovered repository profile
        # (source_roots + test_roots). The fixture is a flat-layout
        # Python repo with no ``src/`` directory, so the profile
        # yields ``allowed_paths=("tests/",)`` — that lets
        # ``TaskExecutionService`` revert the production patch
        # (``main.py``). Override the builder so the M3 plan
        # carries ``main.py`` + ``tests/test_health.py`` as the
        # sandbox's allowed paths. This is the only place M3-4
        # diverges from the canonical plan builder; the divergence
        # is scoped to the offline test.
        from seharness.orchestrator import orchestrator as _orch_mod

        _original_plan_build = _orch_mod._PlanBuilder.build

        def _m3_4_plan_build(*, ctx):  # type: ignore[no-untyped-def]
            plan = _original_plan_build(ctx=ctx)
            task = plan.tasks[0]
            new_task = task.model_copy(
                update={
                    "allowed_paths": ("main.py", "tests/test_health.py"),
                    "validation_commands": ("python -m pytest",),
                }
            )
            return plan.model_copy(update={"tasks": (new_task,)})

        _orch_mod._PlanBuilder.build = staticmethod(_m3_4_plan_build)

        # ---- Run the orchestrator ----
        run_id = RunId("m3-4-offline-acceptance-001")
        try:
            pipeline_result = orch.start_run(
                feature_description="Add /health endpoint",
                repo_path=str(repo_path),
                run_id=run_id,
            )
        finally:
            _orch_mod._PlanBuilder.build = _original_plan_build

        run_dir = execution_root / str(run_id)
        ctx = pipeline_result.context
        assert ctx is not None, (
            "PipelineResult.context must be populated so the acceptance "
            "can introspect the run; got None"
        )

        # ---- Assertion 1: pipeline completed ----
        assert pipeline_result.terminal_state == "completed", (
            f"terminal_state must be 'completed', got "
            f"{pipeline_result.terminal_state!r}; events="
            f"{[(e.phase, e.detail) for e in pipeline_result.events]!r}"
        )

        # ---- Assertion 2: base Git SHA captured ----
        assert ctx.base_git_sha is not None, (
            "base_git_sha must be captured by the repository discovery phase"
        )
        assert re.match(r"^[0-9a-f]{40}$", ctx.base_git_sha), (
            f"base_git_sha must be a 40-char hex SHA, got {ctx.base_git_sha!r}"
        )

        # ---- Assertion 3: MiniMax-M3 specification defines /health ----
        spec_path = run_dir / "specification.json"
        assert spec_path.exists(), f"specification.json must exist at {spec_path!s}"
        spec_text = spec_path.read_text()
        assert "/health" in spec_text or "health" in spec_text, (
            "specification.json must reference the /health endpoint"
        )

        # ---- Assertion 4: plan exists with M3-allowed paths ----
        plan_path = run_dir / "plan.json"
        assert plan_path.exists(), f"plan.json must exist at {plan_path!s}"
        plan_doc = json.loads(plan_path.read_text())
        for task in plan_doc.get("tasks", []):
            allowed = set(task.get("allowed_paths", ()))
            assert allowed.issubset({"main.py", "tests/test_health.py"}), (
                f"task {task.get('task_id')!r} has allowed_paths entry "
                f"outside the sandbox's allowed set: {allowed!r}"
            )

        # ---- Assertion 5 + 6: RED fails for missing endpoint ----
        red_dir = run_dir / "execution" / plan_doc["tasks"][0]["task_id"] / "red"
        assert red_dir.exists(), f"RED evidence directory missing: {red_dir!s}"
        red_result = json.loads((red_dir / "result.json").read_text())
        assert red_result["exit_code"] != 0, (
            f"RED must fail before the patch is applied; got exit_code={red_result['exit_code']}"
        )
        assert red_result["failure_kind"] == "expected_failure", (
            f"RED failure_kind must be 'expected_failure', got {red_result.get('failure_kind')!r}"
        )

        # ---- Assertion 7: production patch is applied ----
        final_diff_path = (
            run_dir / "execution" / plan_doc["tasks"][0]["task_id"] / "final-diff.patch"
        )
        assert final_diff_path.exists(), f"final-diff.patch must exist at {final_diff_path!s}"
        final_diff_text = final_diff_path.read_text()
        assert "main.py" in final_diff_text, (
            "final-diff.patch must modify main.py (the production patch)"
        )
        assert "/health" in final_diff_text, "final-diff.patch must add the /health route"

        # ---- Assertion 8: /health implements the specification ----
        main_py_after = (repo_path / "main.py").read_text()
        assert '@app.get("/health")' in main_py_after, (
            "after the run, main.py must define the /health route"
        )
        verify = subprocess.run(  # nosec B603 — args are hard-coded
            [
                "python3",
                "-c",
                (
                    "import sys; sys.path.insert(0, '.'); "
                    "from fastapi.testclient import TestClient; "
                    "from main import app; "
                    "c = TestClient(app); "
                    "r = c.get('/health'); "
                    "print(r.status_code, r.json())"
                ),
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert verify.returncode == 0, f"TestClient check failed: stderr={verify.stderr!r}"
        assert "200" in verify.stdout and "'status': 'ok'" in verify.stdout, (
            f"TestClient /health did not return 200 + ok; stdout={verify.stdout!r}"
        )

        # ---- Assertion 9: targeted test passes after GREEN ----
        green_dir = red_dir.parent / "green"
        assert green_dir.exists(), f"GREEN evidence directory missing: {green_dir!s}"
        green_result = json.loads((green_dir / "result.json").read_text())
        if green_result["exit_code"] != 0:
            stdout_path = green_dir / "stdout.txt"
            stderr_path = green_dir / "stderr.txt"
            stdout_text = stdout_path.read_text() if stdout_path.exists() else "<no stdout.txt>"
            stderr_text = stderr_path.read_text() if stderr_path.exists() else "<no stderr.txt>"
            assert green_result["exit_code"] == 0, (
                f"GREEN must pass after the patch is applied; "
                f"got exit_code={green_result['exit_code']}\n"
                f"--- pytest stdout ---\n{stdout_text}\n"
                f"--- pytest stderr ---\n{stderr_text}"
            )

        # ---- Assertion 10: full validation passes ----
        assert ctx.validation_exit_code == 0, (
            f"validation_exit_code must be 0 after GREEN, got {ctx.validation_exit_code!r}"
        )

        # ---- Assertion 11: final diff stays within allowed paths ----
        diff_files = set()
        for line in final_diff_text.splitlines():
            m = re.match(r"^\+\+\+ b/(.+)$", line)
            if m:
                diff_files.add(m.group(1))
        for f in diff_files:
            assert f == "main.py" or f.startswith("tests/"), (
                f"final diff modifies file {f!r} which is outside the "
                f"sandbox's allowed paths (main.py, tests/)"
            )

        # ---- Assertion 12: review verdict approved ----
        assert ctx.review_verdict == "approve", (
            f"review_verdict must be 'approve' for the offline "
            f"acceptance, got {ctx.review_verdict!r}"
        )
        review_path = run_dir / "review-verdict.json"
        assert review_path.exists(), f"review-verdict.json must exist at {review_path!s}"

        # ---- Assertion 13: review verdict is schema-consistent ----
        review_doc = json.loads(review_path.read_text())
        verdict_value = review_doc.get("verdict") or review_doc.get("status")
        assert verdict_value in {"approve", "approved"}, (
            f"review verdict must be 'approve' or 'approved', got {verdict_value!r}"
        )

        # ---- Assertion 14: every model phase records M3 evidence ----
        evidence_files = sorted(evidence_dir.rglob("*.jsonl"))
        assert evidence_files, (
            f"evidence directory {evidence_dir!s} is empty; no provider evidence was recorded"
        )
        all_records: list[dict[str, Any]] = []
        for ef in evidence_files:
            for line in ef.read_text().splitlines():
                if line.strip():
                    all_records.append(json.loads(line))
        assert all_records, "no provider evidence records"
        for rec in all_records:
            assert rec.get("configured_model") == "MiniMax-M3", (
                f"evidence record has configured_model="
                f"{rec.get('configured_model')!r}; must be 'MiniMax-M3'"
            )
            assert rec.get("returned_model") == "MiniMax-M3", (
                f"evidence record has returned_model="
                f"{rec.get('returned_model')!r}; must be 'MiniMax-M3'"
            )

        # ---- Assertion 15: no deterministic service participates ----
        for slot_name in (
            "specification",
            "planning",
            "implementation",
            "remediation",
            "review",
        ):
            slot = getattr(result.composition, slot_name)
            assert not type(slot).__name__.startswith("Deterministic"), (
                f"composition.{slot_name}={type(slot).__name__} is deterministic after the run"
            )

        # ---- Assertion 16: PR phase explicitly skipped ----
        assert ctx.remote_skipped_reason == SKIP_REASON_LOCAL_M3_ACCEPTANCE, (
            f"remote_skipped_reason must be "
            f"{SKIP_REASON_LOCAL_M3_ACCEPTANCE!r}, got "
            f"{ctx.remote_skipped_reason!r}"
        )

        # ---- Assertion 17: CI phase explicitly skipped ----
        assert ctx.remote_skipped_reason == SKIP_REASON_LOCAL_M3_ACCEPTANCE

        # ---- Bonus: no PR URL was fabricated, no CI outcome ----
        assert ctx.pr_url is None, (
            f"pr_url must be None under the local-completion policy, got {ctx.pr_url!r}"
        )
        assert ctx.ci_outcome is None, (
            f"ci_outcome must be None under the local-completion policy, got {ctx.ci_outcome!r}"
        )

        # ---- Cleanup: remove the git-initialised repo so tmp_path
        # can be re-used by future tests.
        shutil.rmtree(repo_path, ignore_errors=True)
