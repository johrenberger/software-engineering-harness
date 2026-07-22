"""WP3 (story H) — service protocols + deterministic composition.

The orchestrator now delegates spec / planning / remediation /
review to ``ServiceComposition`` Protocol fields. These tests pin
the wire-level contract:

- ``DeterministicServiceComposition`` is the default and exercises
  the same code paths as the legacy handlers, so existing
  behaviour is preserved bit-for-bit.
- ``ModelBackedServiceComposition`` invokes the ``ModelRouter`` for
  each phase, validates structured output, classifies failure
  kinds, and records provider metadata on a ``ServiceEvidence``
  slot.
- ``ReviewService`` can NEVER auto-approve on malformed output
  (WP3 acceptance criterion: "Review can block delivery and
  cannot always approve").
- The ``ReviewContext`` shape enforces the SPEC's "fresh context"
  rule structurally — there are no history fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from seharness.artifacts.traceability import Plan, RequirementTrace, Task
from seharness.controller.run_ledger import RunLedger
from seharness.delivery.pr import StubPullRequestClient
from seharness.domain.enums import (
    ProviderKind,
    ProviderName,
    RoutingRole,
)
from seharness.domain.requests import ModelRequest
from seharness.domain.results import (
    ModelError,
    ModelResponse,
    ModelUsage,
)
from seharness.models.router import ModelRouter
from seharness.orchestrator import Orchestrator, OrchestratorConfig
from seharness.orchestrator.orchestrator import (
    _phase_planning,
    _phase_review,
    _phase_specification,
)
from seharness.orchestrator.services import (
    DeterministicReviewService,
    DeterministicServiceComposition,
    ImplementationOutcome,
    ImplementationService,
    ModelBackedImplementationService,
    ModelBackedPlanningService,
    ModelBackedRemediationService,
    ModelBackedReviewService,
    ModelBackedServiceComposition,
    ModelBackedSpecificationService,
    PlanningService,
    RemediationOutcome,
    RemediationService,
    ReviewContext,
    ReviewService,
    ReviewVerdict,
    ServiceCallBudget,
    ServiceComposition,
    SpecificationArtifact,
    SpecificationService,
)
from seharness.orchestrator.types import (
    PhaseName,
    PhaseOutcome,
    RunContext,
    new_run_id,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fake_router() -> ModelRouter:
    """Build a ``ModelRouter`` with two stub adapters.

    The tests monkeypatch ``router.invoke`` so the underlying
    adapters never actually run; we just need *some* adapter per
    provider so the router's fallback logic has a real target.
    """

    class _StubAdapter:
        """Minimal ``ModelAdapter`` that the router can iterate over."""

        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        def invoke(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover - never called
            raise RuntimeError("stub adapter: monkeypatch router.invoke instead")

    adapters = {
        ProviderName.MINIMAX: _StubAdapter("minimax"),
        ProviderName.CODEX: _StubAdapter("codex"),
    }
    return ModelRouter(adapters=adapters)


def _ok_response(
    *,
    provider: ProviderName = ProviderName.MINIMAX,
    parsed: Any = None,
    text: str = "ok",
    duration_s: float = 0.01,
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model="test-model",
        raw_output=text,
        parsed=parsed,
        usage=ModelUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        error=None,
        duration_s=duration_s,
        files_changed=(),
    )


def _err_response(
    *,
    provider: ProviderName = ProviderName.MINIMAX,
    kind: str = "provider_failure",
    message: str = "boom",
    retryable: bool = True,
) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model="test-model",
        raw_output="",
        parsed=None,
        usage=None,
        error=ModelError(kind=kind, message=message, retryable=retryable),
        duration_s=0.0,
        files_changed=(),
    )


def _plan() -> Plan:
    return Plan(
        plan_id="plan-test",
        tasks=(
            Task(
                task_id="task-1",
                objective="make the harness great",
                allowed_paths=("src/",),
                validation_commands=("pytest",),
                requirement_traces=(
                    RequirementTrace(requirement_id="FR-test-1", scenario_ids=("SCN-test-1",)),
                ),
            ),
        ),
    )


def _ctx(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id=new_run_id(),
        feature_description="add a WP3 test",
        repo_path=str(tmp_path),
    )


def _spec(name: PhaseName) -> Any:
    from seharness.orchestrator.types import PhaseSpec

    return PhaseSpec(phase=name, run_id="orch-test")


# ---------------------------------------------------------------------------
# Deterministic composition
# ---------------------------------------------------------------------------


class TestDeterministicCompositionDefaults:
    """WP3 (story H): the orchestrator defaults to the deterministic
    composition when no services are injected, so legacy behaviour is
    preserved bit-for-bit."""

    def test_kind_is_deterministic(self) -> None:
        composition = DeterministicServiceComposition()
        assert composition.kind == "deterministic"

    def test_satisfies_service_composition_protocol(self) -> None:
        composition = DeterministicServiceComposition()
        # runtime_checkable lets us assert structural conformance.
        assert isinstance(composition, ServiceComposition)

    def test_each_attribute_is_a_protocol_instance(self) -> None:
        composition = DeterministicServiceComposition()
        assert isinstance(composition.specification, SpecificationService)
        assert isinstance(composition.planning, PlanningService)
        assert isinstance(composition.implementation, ImplementationService)
        assert isinstance(composition.remediation, RemediationService)
        assert isinstance(composition.review, ReviewService)


class TestDeterministicSpecificationService:
    def test_writes_specification_json(self, tmp_path: Path) -> None:
        service = DeterministicServiceComposition().specification
        run_dir = tmp_path / "runs" / "orch-abc"
        run_dir.mkdir(parents=True)
        artifact = service.produce(ctx=_ctx(tmp_path), run_dir=run_dir)
        assert artifact.spec_version == 1
        assert artifact.description == "add a WP3 test"
        spec_path = run_dir / "specification.json"
        assert spec_path.exists()
        payload = json.loads(spec_path.read_text())
        assert payload["description"] == "add a WP3 test"
        assert payload["spec_version"] == 1

    def test_accepts_string_run_dir(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "orch-string"
        DeterministicServiceComposition().specification.produce(
            ctx=_ctx(tmp_path), run_dir=str(run_dir)
        )
        assert (run_dir / "specification.json").exists()


class TestDeterministicReviewService:
    def test_always_approves(self) -> None:
        service = DeterministicReviewService()
        verdict = service.review(
            review_ctx=ReviewContext(
                approved_spec=SpecificationArtifact(
                    spec_version=1,
                    description="d",
                    repo_path="r",
                    run_id="orch-x",
                ),
                impact={},
                plan=_plan(),
                final_diff="",
                validation_results={},
                coverage_results={},
            )
        )
        assert verdict.status == "approved"
        assert verdict.approval is True

    def test_writes_review_verdict_json(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "orch-review"
        run_dir.mkdir(parents=True)
        verdict = DeterministicReviewService().review(
            review_ctx=ReviewContext(
                approved_spec=SpecificationArtifact(
                    spec_version=1,
                    description="d",
                    repo_path="r",
                    run_id="orch-x",
                ),
                impact={},
                plan=_plan(),
                final_diff="",
                validation_results={},
                coverage_results={},
                run_dir=run_dir,
            )
        )
        artifact = run_dir / "review-verdict.json"
        assert artifact.exists()
        payload = json.loads(artifact.read_text())
        assert payload["verdict"] == "approve"
        assert payload["tasks_reviewed"] == ["task-1"]
        assert verdict.status == "approved"


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


class TestOrchestratorWiring:
    def test_orchestrator_uses_deterministic_by_default(self, tmp_path: Path) -> None:
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            pr_client=StubPullRequestClient(),
            trace_writer=None,
        )
        assert isinstance(orch._services, DeterministicServiceComposition)

    def test_specification_phase_delegates_to_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class _CaptureService:
            def produce(self, *, ctx: RunContext, run_dir: Any) -> SpecificationArtifact:
                captured["ctx"] = ctx
                captured["run_dir"] = run_dir
                return SpecificationArtifact(
                    spec_version=1,
                    description=ctx.feature_description or "",
                    repo_path=ctx.repo_path or "",
                    run_id=str(ctx.run_id),
                    provider=ProviderName.MINIMAX,
                    model="test-model",
                )

        composition = DeterministicServiceComposition()
        composition.specification = _CaptureService()  # type: ignore[assignment]
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            services=composition,
            pr_client=StubPullRequestClient(),
            trace_writer=None,
        )
        run_dir = tmp_path / "runs" / "orch-wire-1"
        outcome, _new_ctx, detail = _phase_specification(
            orch, spec=_spec(PhaseName.SPECIFICATION), ctx=_ctx(tmp_path), run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert captured["ctx"].feature_description == "add a WP3 test"
        assert "(provider=minimax)" in detail

    def test_planning_phase_delegates_to_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class _CaptureService:
            def build(self, *, ctx: RunContext) -> Plan:
                captured["ctx"] = ctx
                return _plan()

        composition = DeterministicServiceComposition()
        composition.planning = _CaptureService()  # type: ignore[assignment]
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            services=composition,
            pr_client=StubPullRequestClient(),
            trace_writer=None,
        )
        run_dir = tmp_path / "runs" / "orch-wire-2"
        run_dir.mkdir(parents=True)
        outcome, new_ctx, _detail = _phase_planning(
            orch, spec=_spec(PhaseName.PLANNING), ctx=_ctx(tmp_path), run_dir=run_dir
        )
        assert outcome == PhaseOutcome.OK
        assert captured["ctx"].feature_description == "add a WP3 test"
        assert new_ctx.plan_id == "plan-test"
        assert (run_dir / "plan.json").exists()

    def test_review_phase_falls_back_to_request_changes(self, tmp_path: Path) -> None:
        composition = DeterministicServiceComposition()

        class _Reject(ReviewService):
            def review(self, *, review_ctx: ReviewContext) -> ReviewVerdict:
                return ReviewVerdict(
                    status="changes_requested",
                    approval=False,
                    summary="stub: changes requested",
                )

        composition.review = _Reject()  # type: ignore[assignment]
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(
            run_ledger=ledger,
            config=cfg,
            services=composition,
            pr_client=StubPullRequestClient(),
            trace_writer=None,
        )
        run_dir = tmp_path / "runs" / "orch-review-fail"
        run_dir.mkdir(parents=True)
        outcome, new_ctx, detail = _phase_review(
            orch, spec=_spec(PhaseName.REVIEW), ctx=_ctx(tmp_path), run_dir=run_dir
        )
        assert outcome == PhaseOutcome.FAILED
        assert new_ctx.review_verdict == "request_changes"
        assert "request_changes" in detail


# ---------------------------------------------------------------------------
# Service call budget
# ---------------------------------------------------------------------------


class TestServiceCallBudget:
    def test_default_values_match_spec(self) -> None:
        budget = ServiceCallBudget()
        assert budget.attempts == 3
        assert budget.max_tokens == 4096
        assert budget.max_wall_time_s == 120.0
        assert budget.max_cost_usd == 0.50

    def test_rejects_zero_attempts(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            ServiceCallBudget(attempts=0)

    def test_rejects_zero_max_tokens(self) -> None:
        with pytest.raises(ValueError, match="max_tokens"):
            ServiceCallBudget(max_tokens=0)

    def test_rejects_zero_wall_time(self) -> None:
        with pytest.raises(ValueError, match="max_wall_time_s"):
            ServiceCallBudget(max_wall_time_s=0.0)

    def test_rejects_negative_cost(self) -> None:
        with pytest.raises(ValueError, match="max_cost_usd"):
            ServiceCallBudget(max_cost_usd=-0.01)


# ---------------------------------------------------------------------------
# Model-backed services
# ---------------------------------------------------------------------------


class TestModelBackedSpecificationService:
    def test_writes_specification_json_with_provider_metadata(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        # Cluster N PR4 (spec-plan): the model's parsed payload
        # must satisfy SpecificationSchema — the discovered
        # repo profile name, repository instructions, and
        # validation commands are required, and unknown commands
        # are rejected. The test fixture mirrors what a
        # schema-compliant live MiniMax call would return.
        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={
                    "discovered_repo_profile_name": "software-engineering-harness",
                    "repository_instructions": ("AGENTS.md",),
                    "validation_commands": ("test", "lint"),
                    "description": "x",
                },
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedSpecificationService(router=router)
        run_dir = tmp_path / "runs" / "orch-spec-mb"
        artifact = service.produce(ctx=_ctx(tmp_path), run_dir=run_dir)
        assert artifact.provider == ProviderName.MINIMAX
        assert artifact.model == "test-model"
        assert artifact.input_tokens == 10
        assert artifact.output_tokens == 20
        spec_path = run_dir / "specification.json"
        assert spec_path.exists()
        payload = json.loads(spec_path.read_text())
        assert payload["provider"] == "minimax"
        assert payload["model"] == "test-model"
        # Cluster N PR4: the schema-validated fields are
        # persisted in the artifact too, so downstream phases
        # can read them without re-parsing.
        assert payload["discovered_repo_profile_name"] == ("software-engineering-harness")
        assert payload["repository_instructions"] == ["AGENTS.md"]
        assert payload["validation_commands"] == ["test", "lint"]
        assert service.last_evidence is not None
        assert service.last_evidence.role == RoutingRole.PLANNING

    def test_raises_on_provider_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _err_response(kind="provider_failure", message="503")

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedSpecificationService(router=router)
        with pytest.raises(RuntimeError, match="provider_failure"):
            service.produce(ctx=_ctx(tmp_path), run_dir=tmp_path / "x")


class TestModelBackedImplementationService:
    def test_returns_attempted_outcome_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                parsed={
                    "task_id": "task-1",
                    "attempted_changes": ("src/foo.py",),
                }
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedImplementationService(router=router)
        outcome = service.execute(ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1")
        assert isinstance(outcome, ImplementationOutcome)
        assert outcome.attempted is True
        assert outcome.error_kind is None
        assert service.last_evidence is not None
        assert service.last_evidence.role == RoutingRole.IMPLEMENTATION

    def test_returns_malformed_output_outcome_on_bad_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(parsed={"not": "a task"})

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedImplementationService(router=router)
        outcome = service.execute(ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1")
        assert outcome.error_kind == "malformed_output"
        assert outcome.attempted is True

    def test_surfaces_provider_error_kind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _err_response(kind="timeout", message="slow")

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedImplementationService(router=router)
        outcome = service.execute(ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1")
        assert outcome.error_kind == "timeout"
        assert outcome.error_message == "slow"

    def test_raises_on_unknown_task(self, tmp_path: Path) -> None:
        router = _fake_router()
        service = ModelBackedImplementationService(router=router)
        with pytest.raises(KeyError, match="nope"):
            service.execute(ctx=_ctx(tmp_path), plan=_plan(), task_id="nope")


class TestModelBackedRemediationService:
    def test_classifies_timeout_as_transient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()
        implementation = ModelBackedImplementationService(router=router)

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(parsed={"task_id": "task-1", "classification": "x"})

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedRemediationService(router=router, implementation=implementation)
        prior = ImplementationOutcome(
            attempted=True,
            attempt_index=1,
            final_response=None,
            structured=None,
            error_kind="timeout",
            error_message="slow",
        )
        outcome = service.remediate(
            ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1", prior_outcome=prior
        )
        assert isinstance(outcome, RemediationOutcome)
        assert outcome.classification == "transient"
        assert outcome.attempted is True

    def test_classifies_provider_failure_as_outage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()
        implementation = ModelBackedImplementationService(router=router)

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(parsed={"task_id": "task-1", "classification": "x"})

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedRemediationService(router=router, implementation=implementation)
        prior = ImplementationOutcome(
            attempted=True,
            attempt_index=1,
            final_response=None,
            structured=None,
            error_kind="provider_failure",
            error_message="503",
        )
        outcome = service.remediate(
            ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1", prior_outcome=prior
        )
        assert outcome.classification == "provider_outage"

    def test_classifies_auth_as_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()
        implementation = ModelBackedImplementationService(router=router)

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(parsed={"task_id": "task-1"})

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedRemediationService(router=router, implementation=implementation)
        prior = ImplementationOutcome(
            attempted=True,
            attempt_index=1,
            final_response=None,
            structured=None,
            error_kind="auth",
            error_message="bad key",
        )
        outcome = service.remediate(
            ctx=_ctx(tmp_path), plan=_plan(), task_id="task-1", prior_outcome=prior
        )
        assert outcome.classification == "configuration"


class TestModelBackedReviewService:
    def test_approve_path_persists_verdict_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                parsed={
                    "status": "approved",
                    "approval": True,
                    "summary": "looks good",
                    "findings": (),
                }
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedReviewService(router=router)
        run_dir = tmp_path / "runs" / "orch-review-mb"
        run_dir.mkdir(parents=True)
        verdict = service.review(
            review_ctx=ReviewContext(
                approved_spec=SpecificationArtifact(
                    spec_version=1,
                    description="d",
                    repo_path="r",
                    run_id="orch-x",
                ),
                impact={},
                plan=_plan(),
                final_diff="",
                validation_results={},
                coverage_results={},
                run_dir=run_dir,
            )
        )
        assert verdict.status == "approved"
        assert verdict.approval is True
        payload = json.loads((run_dir / "review-verdict.json").read_text())
        assert payload["verdict"] == "approve"

    def test_changes_requested_path_persists_request_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                parsed={
                    "status": "changes_requested",
                    "approval": False,
                    "summary": "needs work",
                    "findings": ("missing test",),
                }
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedReviewService(router=router)
        run_dir = tmp_path / "runs" / "orch-review-mb"
        run_dir.mkdir(parents=True)
        verdict = service.review(
            review_ctx=ReviewContext(
                approved_spec=SpecificationArtifact(
                    spec_version=1,
                    description="d",
                    repo_path="r",
                    run_id="orch-x",
                ),
                impact={},
                plan=_plan(),
                final_diff="",
                validation_results={},
                coverage_results={},
                run_dir=run_dir,
            )
        )
        assert verdict.status == "changes_requested"
        assert verdict.approval is False
        payload = json.loads((run_dir / "review-verdict.json").read_text())
        assert payload["verdict"] == "request_changes"

    def test_malformed_review_payload_never_auto_approves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(parsed={"not": "a verdict"})

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedReviewService(router=router)
        run_dir = tmp_path / "runs" / "orch-review-malformed"
        run_dir.mkdir(parents=True)
        verdict = service.review(
            review_ctx=ReviewContext(
                approved_spec=SpecificationArtifact(
                    spec_version=1,
                    description="d",
                    repo_path="r",
                    run_id="orch-x",
                ),
                impact={},
                plan=_plan(),
                final_diff="",
                validation_results={},
                coverage_results={},
                run_dir=run_dir,
            )
        )
        # WP3: "Review can block delivery and cannot always approve."
        # A malformed payload MUST downgrade to changes_requested.
        assert verdict.status == "changes_requested"
        assert verdict.approval is False
        payload = json.loads((run_dir / "review-verdict.json").read_text())
        assert payload["verdict"] == "request_changes"

    def test_provider_error_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _err_response(kind="auth", message="bad key")

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedReviewService(router=router)
        with pytest.raises(RuntimeError, match="auth"):
            service.review(
                review_ctx=ReviewContext(
                    approved_spec=SpecificationArtifact(
                        spec_version=1,
                        description="d",
                        repo_path="r",
                        run_id="orch-x",
                    ),
                    impact={},
                    plan=_plan(),
                    final_diff="",
                    validation_results={},
                    coverage_results={},
                )
            )


# ---------------------------------------------------------------------------
# Model-backed composition
# ---------------------------------------------------------------------------


class TestModelBackedServiceComposition:
    def test_kind_is_live(self) -> None:
        router = _fake_router()
        composition = ModelBackedServiceComposition(router=router)
        assert composition.kind == ProviderKind.LIVE.value

    def test_each_service_is_a_protocol_instance(self) -> None:
        router = _fake_router()
        composition = ModelBackedServiceComposition(router=router)
        assert isinstance(composition.specification, SpecificationService)
        assert isinstance(composition.planning, PlanningService)
        assert isinstance(composition.implementation, ImplementationService)
        assert isinstance(composition.remediation, RemediationService)
        assert isinstance(composition.review, ReviewService)


# ---------------------------------------------------------------------------
# Fresh-context rule (SPEC §"Reviewer receives fresh context")
# ---------------------------------------------------------------------------


class TestReviewContextEnforcesFreshContext:
    """The SPEC requires review to receive no prior implementation
    chat history or trace events. ``ReviewContext`` enforces that
    structurally — there are no history fields."""

    def test_review_context_has_no_history_fields(self) -> None:
        from dataclasses import fields

        names = {f.name for f in fields(ReviewContext)}
        # Allowed fields are spec + impact + plan + diff +
        # validation/coverage results + run_dir (for the verdict
        # artifact write).
        assert names == {
            "approved_spec",
            "impact",
            "plan",
            "final_diff",
            "validation_results",
            "coverage_results",
            "run_dir",
        }
        for forbidden in ("chat_history", "trace_events", "messages"):
            assert forbidden not in names, forbidden


# ---------------------------------------------------------------------------
# Review verdict schema
# ---------------------------------------------------------------------------


class TestReviewVerdictSchema:
    """WP3: review can block delivery and cannot always approve.
    The schema is a closed set of statuses."""

    @pytest.mark.parametrize("status", ["approved", "changes_requested", "rejected"])
    def test_status_accepts_canonical_values(self, status: str) -> None:
        ReviewVerdict(status=status, approval=status == "approved", summary="ok")

    @pytest.mark.parametrize("status", ["yes", "no", "lgtm", "APPROVED"])
    def test_status_rejects_unknown_values(self, status: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReviewVerdict(status=status, approval=True, summary="ok")


class TestVerdictToLegacyMapping:
    """The orchestrator's ``_verdict_to_legacy`` keeps the legacy
    ``approve`` / ``request_changes`` / ``reject`` vocabulary so the
    downstream controller + Telegram commands keep working."""

    def test_approved_maps_to_approve(self) -> None:
        from seharness.orchestrator.orchestrator import _verdict_to_legacy

        v = ReviewVerdict(status="approved", approval=True, summary="ok")
        assert _verdict_to_legacy(v) == "approve"

    def test_rejected_maps_to_reject(self) -> None:
        from seharness.orchestrator.orchestrator import _verdict_to_legacy

        v = ReviewVerdict(status="rejected", approval=False, summary="nope")
        assert _verdict_to_legacy(v) == "reject"

    def test_changes_requested_maps_to_request_changes(self) -> None:
        from seharness.orchestrator.orchestrator import _verdict_to_legacy

        v = ReviewVerdict(status="changes_requested", approval=False, summary="fix")
        assert _verdict_to_legacy(v) == "request_changes"


# ---------------------------------------------------------------------------
# Cluster N PR4 — ModelBackedPlanningService
# ---------------------------------------------------------------------------


class TestModelBackedPlanningService:
    """Cluster N PR4 (spec-plan): ``ModelBackedPlanningService``
    validates the model's parsed output against ``PlanSchema`` and
    surfaces ``error_kind=malformed_output`` on schema/policy
    mismatch. The ``build()`` method delegates to the
    deterministic ``_PlanBuilder.build`` for the rich Plan shape
    (requirement traces, validation commands from discovery)."""

    def test_validates_plan_schema_and_passes_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={
                    "plan_id": "plan-spec-plan",
                    "tasks": [
                        {
                            "task_id": "t1",
                            "task_objective": "add readiness gate",
                            "allowed_paths": ["src/"],
                            "order_index": 0,
                        },
                    ],
                },
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedPlanningService(
            router=router,
            policy_allowed_paths=("src/", "tests/"),
        )
        plan = service.build(ctx=_ctx(tmp_path))
        # The validated PlanSchema is cached on the service.
        assert service.last_plan is not None
        assert service.last_plan.plan_id == "plan-spec-plan"
        # The build() method returns the rich Plan from the
        # deterministic builder (requirement traces, validation
        # commands). Cluster N keeps the seam narrow; PR #77
        # wires model-produced tasks into the rich Plan shape.
        assert plan.plan_id.startswith("plan-")
        assert len(plan.tasks) >= 1

    def test_rejects_plan_with_unknown_validation_command_via_police(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even though the schema rejects unknown commands at the
        spec layer, the plan layer additionally enforces the
        policy allowed_paths. A task whose ``allowed_paths``
        includes an out-of-policy entry is rejected."""

        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={
                    "plan_id": "plan-bad",
                    "tasks": [
                        {
                            "task_id": "t1",
                            "task_objective": "deploy",
                            "allowed_paths": ["deploy/"],  # outside policy
                            "order_index": 0,
                        },
                    ],
                },
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedPlanningService(
            router=router,
            policy_allowed_paths=("src/", "tests/"),
        )
        with pytest.raises(RuntimeError, match="plan malformed"):
            service.build(ctx=_ctx(tmp_path))
        assert service.last_evidence is not None
        assert service.last_evidence.error_kind == "malformed_output"

    def test_rejects_malformed_plan_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plan payload that fails ``PlanSchema`` validation
        surfaces ``malformed_output`` per cluster N error
        translation."""

        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={"plan_id": "plan-x"},  # missing tasks
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedPlanningService(
            router=router,
            policy_allowed_paths=("src/", "tests/"),
        )
        with pytest.raises(RuntimeError, match="plan malformed"):
            service.build(ctx=_ctx(tmp_path))
        assert service.last_evidence is not None
        assert service.last_evidence.error_kind == "malformed_output"

    def test_rejects_provider_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Provider-level errors (timeout, 5xx) are NOT
        schema-mismatch errors; they propagate as-is with their
        original error_kind."""

        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _err_response(kind="timeout", message="provider timed out")

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedPlanningService(
            router=router,
            policy_allowed_paths=("src/", "tests/"),
        )
        with pytest.raises(RuntimeError, match="timeout"):
            service.build(ctx=_ctx(tmp_path))
        assert service.last_evidence is not None
        assert service.last_evidence.error_kind == "timeout"


class TestSpecificationServiceRaisesMalformedOutput:
    """Cluster N PR4: ``ModelBackedSpecificationService.produce``
    surfaces ``malformed_output`` on schema mismatch (per the
    cluster-N error translation map)."""

    def test_raises_malformed_output_on_schema_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            # Missing ``discovered_repo_profile_name``; schema
            # should reject.
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={"description": "x"},
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedSpecificationService(router=router)
        with pytest.raises(RuntimeError, match="specification malformed"):
            service.produce(ctx=_ctx(tmp_path), run_dir=tmp_path / "x")
        assert service.last_evidence is not None
        assert service.last_evidence.error_kind == "malformed_output"

    def test_raises_malformed_output_on_unknown_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _fake_router()

        def _fake_invoke(request: ModelRequest) -> ModelResponse:
            return _ok_response(
                provider=ProviderName.MINIMAX,
                parsed={
                    "discovered_repo_profile_name": "x",
                    "description": "y",
                    "validation_commands": ("rm -rf /",),  # unknown
                },
            )

        monkeypatch.setattr(router, "invoke", _fake_invoke)
        service = ModelBackedSpecificationService(router=router)
        with pytest.raises(RuntimeError, match="specification malformed"):
            service.produce(ctx=_ctx(tmp_path), run_dir=tmp_path / "x")
        assert service.last_evidence is not None
        assert service.last_evidence.error_kind == "malformed_output"


# ---------------------------------------------------------------------------
# Cluster M3-1: ReviewVerdict cross-field validation
# ---------------------------------------------------------------------------


class TestReviewVerdictCrossFieldValidation:
    """Cluster M3-1 corrective: status / approval consistency.

    Per the corrective doc:

    - ``approved``          → ``approval == True``
    - ``changes_requested`` → ``approval == False``
    - ``rejected``          → ``approval == False``

    Contradictory payloads must raise ``ValidationError`` so the
    orchestrator can never read an inconsistent completion
    decision.
    """

    def test_approved_with_approval_true_accepted(self) -> None:
        v = ReviewVerdict(
            status="approved",
            approval=True,
            summary="ok",
        )
        assert v.approval is True

    def test_changes_requested_with_approval_false_accepted(self) -> None:
        v = ReviewVerdict(
            status="changes_requested",
            approval=False,
            summary="fix",
        )
        assert v.approval is False

    def test_rejected_with_approval_false_accepted(self) -> None:
        v = ReviewVerdict(
            status="rejected",
            approval=False,
            summary="nope",
        )
        assert v.approval is False

    def test_approved_with_approval_false_rejected(self) -> None:
        """status='approved' requires approval=True; False is
        contradictory and must raise."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReviewVerdict(
                status="approved",
                approval=False,
                summary="bad",
            )

    def test_changes_requested_with_approval_true_rejected(self) -> None:
        """status='changes_requested' requires approval=False;
        True is contradictory."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReviewVerdict(
                status="changes_requested",
                approval=True,
                summary="bad",
            )

    def test_rejected_with_approval_true_rejected(self) -> None:
        """status='rejected' requires approval=False; True is
        contradictory."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ReviewVerdict(
                status="rejected",
                approval=True,
                summary="bad",
            )

    def test_error_message_mentions_inconsistency(self) -> None:
        """The validator's error message must surface the
        inconsistency so operators can diagnose a malformed
        review response without reading Pydantic internals.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            ReviewVerdict(
                status="approved",
                approval=False,
                summary="bad",
            )
        msg = str(excinfo.value)
        assert "inconsistent" in msg.lower() or "status" in msg.lower()
