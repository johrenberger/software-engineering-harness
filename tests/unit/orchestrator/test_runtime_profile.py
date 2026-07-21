"""Tests for the WP2 runtime-profile adapter validator.

Cluster WP2 / story WP2.1: production deployments must refuse to
start with stub adapters; development + test profiles allow them.

These tests pin down the contract for
:func:`seharness.orchestrator.runtime_profile.validate_runtime_profile_adapters`.
The Orchestrator wiring is exercised separately in
``test_orchestrator_runtime_profile_wiring.py`` (this file focuses on
the validator + ``OrchestratorConfig.runtime_profile`` plumbing).
"""

from __future__ import annotations

import pytest

# Imports ordered to avoid a pre-existing circular-import trap: loading
# ``seharness.orchestrator.types`` directly as the FIRST orchestrator-
# related import triggers ``seharness.controller.application_service``'s
# ``from ..orchestrator import Orchestrator`` while ``seharness.orchestrator``
# is still mid-initialising, which fails. Loading a controller module
# first (which is itself triggered by the existing test suite via
# pytest's collection order) avoids the cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401  -- ordering fix

from seharness.config import HarnessConfig, HarnessSection, RuntimeProfile
from seharness.exceptions import ConfigurationError
from seharness.orchestrator.types import OrchestratorConfig
from seharness.orchestrator.runtime_profile import (
    RuntimeProfileDiagnostic,
    STUB_CLASS_MARKERS,
    iter_adapter_slots,
    validate_runtime_profile_adapters,
)


class _StubAdapter:
    """A class whose name contains the 'Stub' marker."""


class _FakeAdapter:
    """A class whose name contains the 'Fake' marker."""


class _NoopAdapter:
    """A class whose name contains the 'Noop' marker (case-insensitive)."""


class _RealAdapter:
    """A class whose name does not contain any stub marker."""


class TestRuntimeProfileEnum:
    def test_all_three_profiles_exist(self) -> None:
        assert RuntimeProfile.DEVELOPMENT.value == "development"
        assert RuntimeProfile.TEST.value == "test"
        assert RuntimeProfile.PRODUCTION.value == "production"

    def test_string_equality(self) -> None:
        """StrEnum: callers can compare against string literals."""
        assert RuntimeProfile("production") == RuntimeProfile.PRODUCTION
        assert RuntimeProfile.PRODUCTION == "production"


class TestStubMarkers:
    def test_markers_cover_known_substrings(self) -> None:
        """The marker list must catch the canonical stub-class prefixes."""
        for marker in ("Stub", "Fake", "Noop", "NoOp", "InMemory"):
            assert marker in STUB_CLASS_MARKERS

    def test_markers_are_substring_match(self) -> None:
        """Markers are substring matches, not equality matches.

        So ``MyStubAdapter`` and ``MyInMemoryFake`` both trip the
        validator, which is what we want for production.
        """


class TestHarnessSectionDefault:
    def test_harness_section_default_profile_is_development(self) -> None:
        """Default profile preserves back-compat for notebook callers."""
        section = HarnessSection()
        assert section.runtime_profile == RuntimeProfile.DEVELOPMENT

    def test_harness_config_round_trip_profile(self) -> None:
        cfg = HarnessConfig(
            harness=HarnessSection(runtime_profile=RuntimeProfile.PRODUCTION)
        )
        assert cfg.harness.runtime_profile == RuntimeProfile.PRODUCTION

    def test_unknown_profile_rejected(self) -> None:
        """Pydantic forbids extra + validates StrEnum strictly."""
        from pydantic import ValidationError as PydValidationError

        with pytest.raises(PydValidationError):
            HarnessSection(runtime_profile="staging")  # type: ignore[arg-type]


class TestOrchestratorConfigDefault:
    def test_default_runtime_profile_is_development(self) -> None:
        cfg = OrchestratorConfig()
        assert cfg.runtime_profile == RuntimeProfile.DEVELOPMENT

    def test_explicit_production_profile_accepted(self) -> None:
        cfg = OrchestratorConfig(runtime_profile=RuntimeProfile.PRODUCTION)
        assert cfg.runtime_profile == RuntimeProfile.PRODUCTION


class TestValidateTestProfile:
    def test_test_profile_silently_allows_stubs(self) -> None:
        """Test profiles never enumerate or warn about stubs."""
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.TEST,
            adapters={
                "pr_client": _StubAdapter(),
                "ci_monitor": _FakeAdapter(),
            },
        )
        assert diag.profile == RuntimeProfile.TEST
        assert diag.stub_adapters == ()

    def test_test_profile_allows_real_adapters(self) -> None:
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.TEST,
            adapters={"pr_client": _RealAdapter()},
        )
        assert diag.stub_adapters == ()


class TestValidateDevelopmentProfile:
    def test_development_profile_enumerates_stubs(self) -> None:
        """Development profiles return a diagnostic so the caller can
        log a single startup warning instead of raising."""
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.DEVELOPMENT,
            adapters={
                "pr_client": _StubAdapter(),
                "ci_monitor": _FakeAdapter(),
            },
        )
        assert diag.profile == RuntimeProfile.DEVELOPMENT
        assert ("pr_client", "_StubAdapter") in diag.stub_adapters
        assert ("ci_monitor", "_FakeAdapter") in diag.stub_adapters

    def test_development_profile_ignores_real_adapters(self) -> None:
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.DEVELOPMENT,
            adapters={"pr_client": _RealAdapter()},
        )
        assert diag.stub_adapters == ()

    def test_development_profile_ignores_none_slots(self) -> None:
        """None slots are a separate check; this validator only catches
        class-name stubs."""
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.DEVELOPMENT,
            adapters={"ci_monitor": None},
        )
        assert diag.stub_adapters == ()


class TestValidateProductionProfile:
    def test_production_profile_rejects_stub(self) -> None:
        """Production refuses to start when a critical slot is a stub."""
        with pytest.raises(ConfigurationError) as exc:
            validate_runtime_profile_adapters(
                profile=RuntimeProfile.PRODUCTION,
                adapters={"pr_client": _StubAdapter()},
            )
        msg = str(exc.value)
        assert "production" in msg
        assert "pr_client" in msg
        assert "_StubAdapter" in msg

    def test_production_profile_rejects_fake(self) -> None:
        with pytest.raises(ConfigurationError) as exc:
            validate_runtime_profile_adapters(
                profile=RuntimeProfile.PRODUCTION,
                adapters={"ci_monitor": _FakeAdapter()},
            )
        assert "_FakeAdapter" in str(exc.value)

    def test_production_profile_rejects_noop(self) -> None:
        """``Noop`` / ``NoOp`` are both caught (case-insensitive)."""
        with pytest.raises(ConfigurationError):
            validate_runtime_profile_adapters(
                profile=RuntimeProfile.PRODUCTION,
                adapters={"runner": _NoopAdapter()},
            )

    def test_production_profile_accepts_real_adapters(self) -> None:
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.PRODUCTION,
            adapters={
                "pr_client": _RealAdapter(),
                "ci_monitor": _RealAdapter(),
                "runner": _RealAdapter(),
            },
        )
        assert diag.profile == RuntimeProfile.PRODUCTION
        assert diag.stub_adapters == ()

    def test_production_profile_lists_all_offending_slots(self) -> None:
        """Error message names every offending slot, not just the first."""
        with pytest.raises(ConfigurationError) as exc:
            validate_runtime_profile_adapters(
                profile=RuntimeProfile.PRODUCTION,
                adapters={
                    "pr_client": _StubAdapter(),
                    "ci_monitor": _FakeAdapter(),
                },
            )
        msg = str(exc.value)
        assert "pr_client" in msg
        assert "ci_monitor" in msg

    def test_production_profile_allows_none_slots(self) -> None:
        """None slots are a separate concern (orchestrator's own init
        rejects ci_monitor=None when required). The validator only
        catches class-name stubs."""
        diag = validate_runtime_profile_adapters(
            profile=RuntimeProfile.PRODUCTION,
            adapters={"ci_monitor": None},
        )
        assert diag.stub_adapters == ()

    def test_production_profile_partial_failure(self) -> None:
        """Mix of real + stub: raises (any stub is a hard fail)."""
        with pytest.raises(ConfigurationError):
            validate_runtime_profile_adapters(
                profile=RuntimeProfile.PRODUCTION,
                adapters={
                    "pr_client": _RealAdapter(),
                    "ci_monitor": _StubAdapter(),
                },
            )


class TestIterAdapterSlots:
    def test_iterates_known_slots(self) -> None:
        """The slot list is a stable contract — any new adapter slot
        that should fail-closed in production must be added here."""

        class _Fake:
            pass

        fake = _Fake()
        slots = dict(
            iter_adapter_slots(
                type(
                    "_OrchLike",
                    (),
                    {
                        "pr_client": fake,
                        "ci_monitor": None,
                        "runner": fake,
                        "trace_writer_active": None,
                    },
                )()
            )
        )
        assert "pr_client" in slots
        assert "ci_monitor" in slots
        assert "runner" in slots
        assert "trace_writer_active" in slots

    def test_missing_slot_is_none(self) -> None:
        """Slots absent on the orchestrator default to None so the
        validator doesn't crash on older configurations."""
        slots = dict(iter_adapter_slots(object()))
        for slot in slots:
            assert slots[slot] is None


class TestDiagnosticFrozen:
    def test_diagnostic_is_immutable(self) -> None:
        """Diagnostics are frozen so they can be safely passed across
        threads and won't be mutated by logging code."""
        diag = RuntimeProfileDiagnostic(
            profile=RuntimeProfile.DEVELOPMENT,
            stub_adapters=(("pr_client", "_StubAdapter"),),
        )
        with pytest.raises((AttributeError, TypeError)):
            diag.profile = RuntimeProfile.PRODUCTION  # type: ignore[misc]
