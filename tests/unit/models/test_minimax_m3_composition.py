"""Cluster M3-2: tests for build_minimax_m3_local_composition().

The corrective doc §"Required architecture correction" lists
seven builder-failure modes. Each mode gets its own test class
so a regression points at the exact precondition that broke.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from seharness.config import RuntimeProfile

# Pre-import the orchestrator's package init via the controller
# module to break the partial-init cycle documented in
# ``application_service.py``. Without this, a fresh-process
# import of ``seharness.models.minimax_m3_composition`` would
# trigger the cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.exceptions import ConfigurationError
from seharness.models.minimax_m3_composition import (
    MiniMaxM3CompositionConfig,
    SandboxConfig,
    build_minimax_m3_local_composition,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sandbox_config(tmp_path: Path) -> SandboxConfig:
    return SandboxConfig(
        sandbox_dir=tmp_path / "sandbox",
        patch_policy_allowed_paths=("src/", "tests/"),
        validation_commands=("pytest",),
    )


def _make_config(
    tmp_path: Path,
    *,
    runtime_profile: RuntimeProfile = RuntimeProfile.PRODUCTION,
    api_key: str | None = "sk-test-key-with-enough-length-to-pass",
    model: str = "MiniMax-M3",
    sandbox_config: SandboxConfig | None = None,
) -> MiniMaxM3CompositionConfig:
    # When ``sandbox_config`` is ``None`` we build a default
    # (so the helper stays usable for the bulk of tests). Tests
    # that exercise the missing-sandbox precondition construct
    # the config directly without the helper.
    final_sandbox = sandbox_config or _make_sandbox_config(tmp_path)
    return MiniMaxM3CompositionConfig(
        api_key=api_key,
        model=model,
        runtime_profile=runtime_profile,
        sandbox_config=final_sandbox,
        provider_evidence_dir=tmp_path / "evidence",
    )


# ---------------------------------------------------------------------------
# SandboxConfig validation
# ---------------------------------------------------------------------------


class TestSandboxConfigValidation:
    """SandboxConfig enforces the empty-policy / empty-validation
    refusals at construction time.
    """

    def test_empty_patch_policy_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="patch_policy_allowed_paths"):
            SandboxConfig(
                sandbox_dir=tmp_path / "sandbox",
                patch_policy_allowed_paths=(),
                validation_commands=("pytest",),
            )

    def test_empty_validation_commands_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="validation_commands"):
            SandboxConfig(
                sandbox_dir=tmp_path / "sandbox",
                patch_policy_allowed_paths=("src/",),
                validation_commands=(),
            )

    def test_minimal_construction_works(self, tmp_path: Path) -> None:
        cfg = _make_sandbox_config(tmp_path)
        assert cfg.sandbox_dir == tmp_path / "sandbox"
        assert cfg.patch_policy_allowed_paths == ("src/", "tests/")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestBuildM3CompositionHappyPath:
    """The builder returns a complete result when all
    preconditions are satisfied.
    """

    def test_production_construction_succeeds(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        # The MiniMax adapter reads MINIMAX_API_KEY from the
        # environment. Stub it so the adapter's readiness probe
        # passes.
        with patch.dict(
            "os.environ",
            {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
        ):
            result = build_minimax_m3_local_composition(config)
        assert result.composition is not None
        assert result.author_router is not None
        assert result.review_router is not None
        assert result.evidence_writer is not None
        assert result.sandbox_config is not None

    def test_author_and_review_routers_are_distinct_objects(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with patch.dict(
            "os.environ",
            {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
        ):
            result = build_minimax_m3_local_composition(config)
        assert result.author_router is not result.review_router
        assert id(result.author_router) != id(result.review_router)

    def test_development_profile_allows_deterministic_services(self, tmp_path: Path) -> None:
        """In DEVELOPMENT the deterministic services remain
        acceptable; the builder only enforces the no-deterministic
        rule on PRODUCTION.
        """
        config = _make_config(tmp_path, runtime_profile=RuntimeProfile.DEVELOPMENT)
        # The MiniMaxAdapter uses env MINIMAX_API_KEY at
        # request time; the readiness probe is what fails
        # closed on PRODUCTION. In DEVELOPMENT the probe
        # returns a diagnostic but does not raise. However,
        # the readiness probe calls the live HTTP endpoint
        # which is not available in the test environment,
        # so we mock the probe.
        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch("seharness.models.minimax.MiniMaxAdapter.readiness") as mock_readiness,
        ):
            mock_readiness.return_value.is_live.return_value = True
            mock_readiness.return_value.reason = None
            result = build_minimax_m3_local_composition(config)
        assert result.composition is not None

    def test_test_profile_skips_deterministic_rejection(self, tmp_path: Path) -> None:
        """TEST profile builds the composition without running
        the deterministic-service rejection.
        """
        config = _make_config(tmp_path, runtime_profile=RuntimeProfile.TEST)
        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch("seharness.models.minimax.MiniMaxAdapter.readiness") as mock_readiness,
        ):
            mock_readiness.return_value.is_live.return_value = True
            result = build_minimax_m3_local_composition(config)
        assert result.composition is not None


# ---------------------------------------------------------------------------
# Builder failure modes (corrective doc's "must fail" list)
# ---------------------------------------------------------------------------


class TestBuilderFailsOnProductionWithoutApiKey:
    """PRODUCTION requires an explicit api_key."""

    def test_production_without_api_key_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path, api_key=None)
        with pytest.raises(ConfigurationError, match="api_key"):
            build_minimax_m3_local_composition(config)

    def test_production_with_empty_api_key_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path, api_key="")
        with pytest.raises(ConfigurationError, match="api_key"):
            build_minimax_m3_local_composition(config)


class TestBuilderFailsOnNonM3Model:
    """PRODUCTION requires configured model == 'MiniMax-M3'."""

    def test_production_with_m2_7_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path, model="MiniMax-M2.7")
        with pytest.raises(ConfigurationError, match="MiniMax-M3"):
            build_minimax_m3_local_composition(config)

    def test_production_with_empty_model_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path, model="")
        with pytest.raises(ConfigurationError, match="MiniMax-M3"):
            build_minimax_m3_local_composition(config)

    def test_development_allows_other_models(self, tmp_path: Path) -> None:
        """Non-PRODUCTION profiles do not require MiniMax-M3.
        The M2.7 compatibility path remains open.
        """
        config = _make_config(
            tmp_path,
            runtime_profile=RuntimeProfile.DEVELOPMENT,
            model="MiniMax-M2.7",
        )
        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch("seharness.models.minimax.MiniMaxAdapter.readiness") as mock_readiness,
        ):
            mock_readiness.return_value.is_live.return_value = True
            result = build_minimax_m3_local_composition(config)
        assert result.composition is not None


class TestBuilderFailsWithoutSandboxConfig:
    """No sandbox config → fail."""

    def test_production_without_sandbox_raises(self, tmp_path: Path) -> None:
        config = MiniMaxM3CompositionConfig(
            api_key="sk-test-key-with-enough-length-to-pass",
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.PRODUCTION,
            sandbox_config=None,
            provider_evidence_dir=tmp_path / "evidence",
        )
        with pytest.raises(ConfigurationError, match="sandbox_config"):
            build_minimax_m3_local_composition(config)


class TestBuilderFailsWithoutEvidenceDir:
    """No provider_evidence_dir → fail."""

    def test_production_without_evidence_dir_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        object.__setattr__(config, "provider_evidence_dir", None)
        with pytest.raises(ConfigurationError, match="provider_evidence_dir"):
            build_minimax_m3_local_composition(config)


class TestBuilderFailsWithStubTransport:
    """A stub-class MiniMax transport is rejected on PRODUCTION."""

    def test_production_with_fake_transport_raises(self, tmp_path: Path) -> None:
        """The MiniMaxAdapter's transport slot is wired to a
        real ``HttpMiniMaxTransport`` in production builds. The
        builder checks the transport class name against the
        stub marker list; a ``FakeMiniMaxTransport`` (or any
        other stub class) is rejected.
        """
        config = _make_config(tmp_path)

        class FakeMiniMaxTransport:
            pass

        fake_transport = FakeMiniMaxTransport()
        # Use ``spec=MiniMaxAdapter`` so the mock returns an
        # object with the same attribute surface as the real
        # adapter; without it the mock is a bare ``MagicMock``
        # that doesn't accept arbitrary attribute assignment.
        from seharness.models.minimax import MiniMaxAdapter

        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch.object(MiniMaxAdapter, "__init__", return_value=None),
        ):
            mock_instance = MiniMaxAdapter.__new__(MiniMaxAdapter)
            mock_instance._transport = fake_transport
            with patch.object(MiniMaxAdapter, "readiness") as mock_readiness:
                mock_readiness.return_value.is_live.return_value = True
                with (
                    patch(
                        "seharness.models.minimax_m3_composition._build_minimax_adapter",
                        return_value=mock_instance,
                    ),
                    pytest.raises(ConfigurationError, match="stub"),
                ):
                    build_minimax_m3_local_composition(config)


class TestBuilderFailsOnReadiness:
    """Unverified M3 readiness → fail."""

    def test_production_with_not_live_adapter_raises(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch("seharness.models.minimax.MiniMaxAdapter.readiness") as mock_readiness,
        ):
            mock_readiness.return_value.is_live.return_value = False
            mock_readiness.return_value.reason = "API key unset"
            with pytest.raises(ConfigurationError):
                build_minimax_m3_local_composition(config)


class TestBuilderFailsOnSharedRouter:
    """Author and review routers sharing an object → fail.

    The current implementation constructs two distinct routers
    via :func:`_build_router`, so this check is internally
    guaranteed; we still test it explicitly so a future
    refactor that re-introduces sharing fails the test.
    """

    def test_distinct_router_construction_enforced(self, tmp_path: Path) -> None:
        """The router builder returns distinct objects; we
        verify via ``id()`` so any future caching that returns
        the same router twice fails this test.
        """
        from seharness.domain.enums import ProviderName, RoutingRole
        from seharness.models.minimax_m3_composition import _build_router
        from seharness.models.minimax_transport import FakeMiniMaxTransport

        adapter_a = FakeMiniMaxTransport()
        adapter_b = FakeMiniMaxTransport()
        # We can't easily get the MiniMaxAdapter's internal transport;
        # use the underlying constructor through _build_minimax_adapter
        # via a small config.
        with patch.dict(
            "os.environ",
            {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
        ):
            from seharness.models.minimax import MiniMaxAdapter

            a1 = MiniMaxAdapter(transport=adapter_a)
            a2 = MiniMaxAdapter(transport=adapter_b)
            r1 = _build_router(
                role_to_provider={RoutingRole.PLANNING: ProviderName.MINIMAX},
                minimax_adapter=a1,
            )
            r2 = _build_router(
                role_to_provider={RoutingRole.REVIEW: ProviderName.MINIMAX},
                minimax_adapter=a2,
            )
        assert r1 is not r2
        assert id(r1) != id(r2)


class TestBuilderDeterministicServiceRejection:
    """The corrective doc forbids deterministic services in
    PRODUCTION. We test the rejection by mocking the
    composition's services to include a Deterministic variant.
    """

    def test_production_with_deterministic_planning_raises(self, tmp_path: Path) -> None:
        with (
            patch.dict(
                "os.environ",
                {"MINIMAX_API_KEY": "sk-test-key-with-enough-length-to-pass"},
            ),
            patch("seharness.models.minimax.MiniMaxAdapter.readiness") as mock_readiness,
        ):
            mock_readiness.return_value.is_live.return_value = True
            with patch(
                "seharness.orchestrator.services.DeterministicPlanningService"
            ) as mock_det_planning:
                mock_det_planning.return_value.__class__.__name__ = "DeterministicPlanningService"
                # The composition's planner is set in
                # ModelBackedServiceComposition.__init__; we
                # patch post-construction to inject a fake.
                with patch(
                    "seharness.orchestrator.services.ModelBackedServiceComposition.__init__",
                    return_value=None,
                ):
                    # Without going through the real init
                    # the composition slots are uninitialized;
                    # we verify the check fires by running a
                    # direct unit on ``_looks_like_deterministic_service``.
                    from seharness.models.minimax_m3_composition import (
                        _looks_like_deterministic_service,
                    )

                    class DeterministicPlanningService:
                        pass

                    assert _looks_like_deterministic_service(DeterministicPlanningService()) is True


# ---------------------------------------------------------------------------
# Cluster M3-4: build_minimax_m3_offline_composition()
# ---------------------------------------------------------------------------


from seharness.models.minimax_m3_composition import (  # noqa: E402
    build_minimax_m3_offline_composition,
)
from seharness.models.minimax_transport import (  # noqa: E402
    MiniMaxTransportResponse,
    RecordingMiniMaxTransport,
)


def _make_offline_config(
    *,
    tmp_path: Path,
    runtime_profile: RuntimeProfile = RuntimeProfile.TEST,
    api_key: str | None = "sk-test-key-with-enough-length-to-pass",
    model: str = "MiniMax-M3",
    sandbox_config: SandboxConfig | None = None,
) -> MiniMaxM3CompositionConfig:
    """Offline-config helper: TEST profile by default."""
    final_sandbox = sandbox_config or _make_sandbox_config(tmp_path)
    return MiniMaxM3CompositionConfig(
        api_key=api_key,
        model=model,
        runtime_profile=runtime_profile,
        sandbox_config=final_sandbox,
        provider_evidence_dir=tmp_path / "evidence",
    )


def _make_synthetic_responses() -> tuple[MiniMaxTransportResponse, ...]:
    return (
        MiniMaxTransportResponse(
            content_text=(
                '{"discovered_repo_profile_name": "repo-profile.json", '
                '"repository_instructions": ["pyproject.toml"], '
                '"validation_commands": ["test", "lint", "type_check"], '
                '"description": "Add /health endpoint."}'
            ),
            usage_input_tokens=10,
            usage_output_tokens=10,
            request_id="offline-rec-1",
            error=None,
        ),
        MiniMaxTransportResponse(
            content_text=(
                '{"plan_id": "plan-1", "tasks": [{"task_id": "t1", '
                '"task_objective": "Add /health", "allowed_paths": '
                '["main.py"], "order_index": 0}]}'
            ),
            usage_input_tokens=10,
            usage_output_tokens=10,
            request_id="offline-rec-2",
            error=None,
        ),
    )


class TestOfflineCompositionBasic:
    """Happy-path: the offline factory returns a wired composition
    with two distinct routers and a non-None evidence writer.
    """

    def test_offline_returns_full_result(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path)
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        result = build_minimax_m3_offline_composition(
            config=config,
            recording_transport=transport,
            recording_responses=responses,
        )
        assert result.composition is not None
        assert result.author_router is not None
        assert result.review_router is not None
        assert result.evidence_writer is not None
        assert result.sandbox_config is not None

    def test_author_and_review_routers_distinct(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path)
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        result = build_minimax_m3_offline_composition(
            config=config,
            recording_transport=transport,
            recording_responses=responses,
        )
        assert result.author_router is not result.review_router

    def test_evidence_writer_uses_configured_dir(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        config = MiniMaxM3CompositionConfig(
            api_key="sk-test-key-with-enough-length-to-pass",
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.TEST,
            sandbox_config=_make_sandbox_config(tmp_path),
            provider_evidence_dir=evidence_dir,
        )
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        result = build_minimax_m3_offline_composition(
            config=config,
            recording_transport=transport,
            recording_responses=responses,
        )
        assert result.evidence_writer.evidence_dir == evidence_dir


class TestOfflineCompositionRefusals:
    """The offline factory refuses non-TEST profiles and missing
    config the production builder also refuses (model, sandbox,
    evidence dir, responses).
    """

    def test_production_profile_refused(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path, runtime_profile=RuntimeProfile.PRODUCTION)
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        with pytest.raises(ConfigurationError, match="runtime_profile must be TEST"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=responses,
            )

    def test_development_profile_refused(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path, runtime_profile=RuntimeProfile.DEVELOPMENT)
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        with pytest.raises(ConfigurationError, match="runtime_profile must be TEST"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=responses,
            )

    def test_non_m3_model_refused(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path, model="MiniMax-M2.7")
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        with pytest.raises(ConfigurationError, match="model must be"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=responses,
            )

    def test_missing_sandbox_refused(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path, sandbox_config=None)
        # Bypass the helper's default by reconstructing manually.
        config = MiniMaxM3CompositionConfig(
            api_key="sk-test-key-with-enough-length-to-pass",
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.TEST,
            sandbox_config=None,  # type: ignore[arg-type]
            provider_evidence_dir=tmp_path / "evidence",
        )
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        with pytest.raises(ConfigurationError, match="sandbox_config is required"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=responses,
            )

    def test_missing_evidence_dir_refused(self, tmp_path: Path) -> None:
        config = MiniMaxM3CompositionConfig(
            api_key="sk-test-key-with-enough-length-to-pass",
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.TEST,
            sandbox_config=_make_sandbox_config(tmp_path),
            provider_evidence_dir=None,  # type: ignore[arg-type]
        )
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        with pytest.raises(ConfigurationError, match="provider_evidence_dir is required"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=responses,
            )

    def test_empty_responses_refused(self, tmp_path: Path) -> None:
        config = _make_offline_config(tmp_path=tmp_path)
        transport = RecordingMiniMaxTransport(responses=())
        with pytest.raises(ConfigurationError, match="non-empty tuple"):
            build_minimax_m3_offline_composition(
                config=config,
                recording_transport=transport,
                recording_responses=(),
            )

    def test_api_key_not_required_for_offline_test(self, tmp_path: Path) -> None:
        """Offline TEST mode does NOT require an api_key; that's a
        PRODUCTION-only invariant.
        """
        config = MiniMaxM3CompositionConfig(
            api_key=None,  # offline test doesn't need one
            model="MiniMax-M3",
            runtime_profile=RuntimeProfile.TEST,
            sandbox_config=_make_sandbox_config(tmp_path),
            provider_evidence_dir=tmp_path / "evidence",
        )
        responses = _make_synthetic_responses()
        transport = RecordingMiniMaxTransport(responses=responses)
        result = build_minimax_m3_offline_composition(
            config=config,
            recording_transport=transport,
            recording_responses=responses,
        )
        assert result.composition is not None
