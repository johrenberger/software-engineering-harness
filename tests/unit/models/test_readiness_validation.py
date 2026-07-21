"""Cluster N PR3 \u2014 readiness validation for production router wiring.

Tests for ``seharness.models.readiness_validation``. The validator
walks a router's wired adapters and refuses to start in
production when any adapter's ``readiness().is_live()`` returns
``False``. This replaces the legacy class-name substring
detection (``\"Live\"``, `\"Fake\"`) which was fooled by
``MiniMaxAdapter`` declaring ``kind = ProviderKind.LIVE`` while
its transport was actually a stub.

The fixture surface used here is intentionally minimal: the
validator only needs ``router.adapters`` returning a mapping of
provider name \u2192 ``ModelAdapter``. The tests below wire
adapters via the real ``ModelRouter`` plus ``FakeModelAdapter``
and ``MiniMaxAdapter`` (with the cluster-N transport
injection).
"""

from __future__ import annotations

import os

import pytest

# Trigger the canonical import order before importing
# ``seharness.orchestrator.services``. Importing the orchestrator
# sub-modules before ``seharness.controller.run_ledger`` triggers
# a pre-existing circular import in the controller package
# (``seharness.controller.application_service`` → ``seharness.
# orchestrator`` → ``seharness.controller.run_ledger``); the
# canonical import order works, the reverse does not.
import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.config import RuntimeProfile
from seharness.domain.enums import ProviderName
from seharness.exceptions import ConfigurationError
from seharness.models import (
    FakeMiniMaxTransport,
    MiniMaxAdapter,
    ReadinessDiagnostic,
    validate_router_readiness,
)
from seharness.models.router import ModelRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _NotLiveAdapter:
    """Adapter that does not implement ``readiness()``.

    Used to verify the validator surfaces a synthetic not-live
    readiness for legacy adapters, rather than passing them
    silently."""

    @property
    def name(self) -> str:
        return "not-live-legacy"

    def invoke(self, request):  # pragma: no cover - never called
        raise RuntimeError("not used by these tests")


class _RaisesReadinessAdapter:
    """Adapter whose ``readiness()`` raises.

    Used to verify the validator catches exceptions and
    surfaces them as not-live."""

    @property
    def name(self) -> str:
        return "raises-readiness"

    def readiness(self):
        raise RuntimeError("boom")

    def invoke(self, request):  # pragma: no cover - never called
        raise RuntimeError("not used")


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip cluster-N env vars so each test starts fresh."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)


def _router_with_minimax(
    *,
    transport: object,
    model_identifier: str,
) -> ModelRouter:
    """Build a ``ModelRouter`` with a single MiniMax adapter.

    The MiniMax adapter is constructed with the supplied
    transport (Fake for offline tests) so we can exercise the
    readiness contract directly.
    """
    adapter = MiniMaxAdapter(
        transport=transport,
        model_identifier=model_identifier,
    )
    return ModelRouter(adapters={ProviderName.MINIMAX: adapter})


# ---------------------------------------------------------------------------
# Happy path: live adapter passes through every profile
# ---------------------------------------------------------------------------


class TestLiveAdapterPassesAllProfiles:
    def test_http_transport_live_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An ``HttpMiniMaxTransport`` with a key, a model id, and
        ``MINIMAX_MODEL`` env var resolves to live in
        production. The validator returns an empty diagnostic."""

        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        # HttpMiniMaxTransport reads the key from env at call
        # time; readiness probes only check class membership.
        from seharness.models.minimax_transport import HttpMiniMaxTransport

        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY"),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        diag = validate_router_readiness(
            profile=RuntimeProfile.PRODUCTION,
            router=router,
        )
        assert isinstance(diag, ReadinessDiagnostic)
        assert diag.profile == RuntimeProfile.PRODUCTION
        assert diag.not_live_adapters == ()

    def test_http_transport_live_in_development(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        from seharness.models.minimax_transport import HttpMiniMaxTransport

        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY"),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        diag = validate_router_readiness(
            profile=RuntimeProfile.DEVELOPMENT,
            router=router,
        )
        assert diag.not_live_adapters == ()

    def test_http_transport_live_in_test(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        from seharness.models.minimax_transport import HttpMiniMaxTransport

        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY"),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        diag = validate_router_readiness(
            profile=RuntimeProfile.TEST,
            router=router,
        )
        # TEST silently passes; no enumeration.
        assert diag.not_live_adapters == ()


# ---------------------------------------------------------------------------
# Production: not-live raises
# ---------------------------------------------------------------------------


class TestNotLiveRaisesInProduction:
    def test_no_api_key_raises_in_production(self) -> None:
        """When ``MINIMAX_API_KEY`` is unset, ``MiniMaxAdapter``
        constructs an ``HttpMiniMaxTransport`` that cannot call
        the provider. Production startup refuses to start."""

        os.environ.pop("MINIMAX_API_KEY", None)
        adapter = MiniMaxAdapter(model_identifier="MiniMax-M2.7")
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        # The error message names the provider and the reason.
        msg = str(excinfo.value)
        assert "minimax" in msg
        assert "not live" in msg
        assert "MINIMAX_API_KEY" in msg

    def test_fake_transport_raises_in_production(self) -> None:
        """A ``FakeMiniMaxTransport`` (or ``RecordingMiniMaxTransport``)
        is not the production HTTP transport. Production startup
        refuses to start even when the key + model are set."""

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        msg = str(excinfo.value)
        assert "minimax" in msg
        # The reason should mention the transport mismatch.
        assert "production HTTP transport" in msg or "transport" in msg

    def test_empty_model_id_raises_in_production(self) -> None:
        """When ``model_identifier`` is empty and ``MINIMAX_MODEL``
        is unset, the adapter is not configured for any model.
        Production startup refuses to start."""

        os.environ.pop("MINIMAX_API_KEY", None)
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(model_identifier="")
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        msg = str(excinfo.value)
        assert "minimax" in msg
        # Either no key OR empty model id is a valid reason.
        assert "not live" in msg

    def test_legacy_adapter_without_readiness_raises_in_production(self) -> None:
        """Adapters that pre-date cluster N (no ``readiness()``
        method) are surfaced as not-live in production so the
        legacy ``MiniMaxAdapter.invoke()`` stub is no longer
        accepted as a live adapter."""

        router = ModelRouter(adapters={"legacy": _NotLiveAdapter()})
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        msg = str(excinfo.value)
        assert "legacy" in msg
        assert "readiness" in msg

    def test_adapter_raising_readiness_raises_in_production(self) -> None:
        """An adapter whose ``readiness()`` raises is captured
        as not-live; production startup refuses to start."""

        router = ModelRouter(adapters={"flaky": _RaisesReadinessAdapter()})
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        msg = str(excinfo.value)
        assert "flaky" in msg
        assert "boom" in msg

    def test_multiple_not_live_adapters_all_listed(self) -> None:
        """When multiple adapters are not-live, the error message
        names every offending (provider, reason) pair so the
        operator can fix each one in turn."""

        os.environ.pop("MINIMAX_API_KEY", None)
        os.environ.pop("MINIMAX_MODEL", None)
        router = ModelRouter(
            adapters={
                "a": _NotLiveAdapter(),
                "b": MiniMaxAdapter(model_identifier=""),
            }
        )
        with pytest.raises(ConfigurationError) as excinfo:
            validate_router_readiness(
                profile=RuntimeProfile.PRODUCTION,
                router=router,
            )
        msg = str(excinfo.value)
        assert "a(" in msg
        assert "b(" in msg


# ---------------------------------------------------------------------------
# Development: not-live returns diagnostic, does not raise
# ---------------------------------------------------------------------------


class TestNotLiveReturnsDiagnosticInDevelopment:
    def test_fake_transport_returns_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """In ``DEVELOPMENT`` the validator does NOT raise; it
        returns a diagnostic listing the not-live adapter so the
        caller can log a single startup warning."""

        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        diag = validate_router_readiness(
            profile=RuntimeProfile.DEVELOPMENT,
            router=router,
        )
        assert diag.profile == RuntimeProfile.DEVELOPMENT
        assert len(diag.not_live_adapters) == 1
        provider, reason = diag.not_live_adapters[0]
        assert provider == ProviderName.MINIMAX.value
        assert "production HTTP transport" in reason

    def test_no_key_returns_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        adapter = MiniMaxAdapter(model_identifier="MiniMax-M2.7")
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        diag = validate_router_readiness(
            profile=RuntimeProfile.DEVELOPMENT,
            router=router,
        )
        assert len(diag.not_live_adapters) == 1
        _, reason = diag.not_live_adapters[0]
        assert "MINIMAX_API_KEY" in reason


# ---------------------------------------------------------------------------
# Test: silent no-op
# ---------------------------------------------------------------------------


class TestTestProfileIsSilentNoOp:
    def test_test_profile_does_not_enumerate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``TEST`` profile is intentionally silent: tests wire
        fakes deliberately, and we don't want a per-test
        diagnostic noise. The validator returns an empty
        diagnostic regardless of adapter state."""

        _clean_env(monkeypatch)
        # Wire a not-live adapter; TEST should not flag it.
        router = ModelRouter(
            adapters={
                ProviderName.MINIMAX: MiniMaxAdapter(
                    transport=FakeMiniMaxTransport(),
                    model_identifier="MiniMax-M2.7",
                ),
            }
        )
        diag = validate_router_readiness(
            profile=RuntimeProfile.TEST,
            router=router,
        )
        assert diag.profile == RuntimeProfile.TEST
        assert diag.not_live_adapters == ()


# ---------------------------------------------------------------------------
# Helper: iter_not_live_adapters
# ---------------------------------------------------------------------------


class TestIterNotLiveAdapters:
    def test_yields_only_not_live_adapters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        from seharness.models.minimax_transport import HttpMiniMaxTransport

        live_adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY"),
            model_identifier="MiniMax-M2.7",
        )
        not_live_adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(
            adapters={
                "live": live_adapter,
                "not-live": not_live_adapter,
            }
        )
        from seharness.models import iter_not_live_adapters

        results = list(iter_not_live_adapters(router))
        assert len(results) == 1
        provider, readiness = results[0]
        assert provider == "not-live"
        assert not readiness.is_live()


# ---------------------------------------------------------------------------
# ``ModelBackedServiceComposition`` integration
# ---------------------------------------------------------------------------


class TestModelBackedCompositionIntegratesReadinessGate:
    """``ModelBackedServiceComposition`` invokes the readiness
    validator in its ``__init__`` when ``runtime_profile`` is
    supplied. PRODUCTION must raise; DEVELOPMENT must record the
    diagnostic; TEST must pass silently; ``None`` must skip the
    validator (back-compat with existing callers)."""

    def test_production_raises_with_not_live_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.services import ModelBackedServiceComposition

        _clean_env(monkeypatch)
        # Fake transport with key + model -> not-live for
        # production.
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        with pytest.raises(ConfigurationError):
            ModelBackedServiceComposition(
                router=router,
                runtime_profile=RuntimeProfile.PRODUCTION,
            )

    def test_production_succeeds_with_http_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.services import ModelBackedServiceComposition

        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        from seharness.models.minimax_transport import HttpMiniMaxTransport

        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY"),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        composition = ModelBackedServiceComposition(
            router=router,
            runtime_profile=RuntimeProfile.PRODUCTION,
        )
        # Empty diagnostic on success.
        assert composition.last_readiness_diagnostic is not None
        assert composition.last_readiness_diagnostic.not_live_adapters == ()

    def test_development_records_diagnostic_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seharness.orchestrator.services import ModelBackedServiceComposition

        _clean_env(monkeypatch)
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        composition = ModelBackedServiceComposition(
            router=router,
            runtime_profile=RuntimeProfile.DEVELOPMENT,
        )
        # Composition was constructed; the diagnostic lists the
        # not-live adapter for the caller to log.
        assert composition.last_readiness_diagnostic is not None
        assert len(composition.last_readiness_diagnostic.not_live_adapters) == 1

    def test_test_profile_passes_silently(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.services import ModelBackedServiceComposition

        _clean_env(monkeypatch)
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        composition = ModelBackedServiceComposition(
            router=router,
            runtime_profile=RuntimeProfile.TEST,
        )
        # TEST: diagnostic is recorded but the ``not_live_adapters``
        # tuple is empty by design.
        assert composition.last_readiness_diagnostic is not None
        assert composition.last_readiness_diagnostic.not_live_adapters == ()

    def test_none_profile_skips_validator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing callers that do not supply ``runtime_profile``
        (e.g. older tests) must keep working. The validator is
        not invoked and ``last_readiness_diagnostic`` stays
        ``None``."""

        from seharness.orchestrator.services import ModelBackedServiceComposition

        _clean_env(monkeypatch)
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        router = ModelRouter(adapters={ProviderName.MINIMAX: adapter})
        composition = ModelBackedServiceComposition(router=router)
        assert composition.last_readiness_diagnostic is None
        # The composition is still constructed; services are wired.
        assert composition.specification is not None
        assert composition.implementation is not None
