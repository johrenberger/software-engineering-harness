"""Mutation killers for slice 4 Pydantic config mutations.

Per SPEC §"Mandatory Mutation Testing" the model-layer Pydantic models must
not allow extra fields, must be frozen after construction, must validate on
assignment, and must have tight bounds on numeric fields. These tests
exercise those invariants directly so that mutations on the
``ConfigDict(...)`` and ``Field(...)`` kwargs are caught.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.domain.enums import ProviderName, RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.domain.results import (
    ModelError,
    ModelRepair,
    ModelResponse,
    ModelUsage,
)


class TestModelRequestConfigKillers:
    def test_rejects_extra_field(self) -> None:
        """Mutations on extra='forbid' must be killed."""
        with pytest.raises(ValidationError):
            ModelRequest(
                role=RoutingRole.PLANNING,
                prompt="x",
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_min_tokens_enforced(self) -> None:
        """Mutations on max_tokens Field(ge=1, ...) must be killed."""
        with pytest.raises(ValidationError):
            ModelRequest(
                role=RoutingRole.PLANNING,
                prompt="x",
                max_tokens=0,
            )

    def test_max_tokens_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(
                role=RoutingRole.PLANNING,
                prompt="x",
                max_tokens=1_000_001,
            )

    def test_temperature_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(
                role=RoutingRole.PLANNING,
                prompt="x",
                temperature=-0.1,
            )

    def test_temperature_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(
                role=RoutingRole.PLANNING,
                prompt="x",
                temperature=2.5,
            )

    def test_is_frozen(self) -> None:
        """Mutations on frozen=True must be killed."""
        req = ModelRequest(role=RoutingRole.PLANNING, prompt="x")
        with pytest.raises(ValidationError):
            req.prompt = "mutated"  # type: ignore[misc]

    def test_validates_on_assignment(self) -> None:
        """Mutations on validate_assignment=True must be killed."""
        req = ModelRequest(role=RoutingRole.PLANNING, prompt="x")
        with pytest.raises(ValidationError):
            req.role = "invalid"  # type: ignore[assignment]


class TestModelResponseConfigKillers:
    def test_rejects_extra_field(self) -> None:
        """Mutations on extra='forbid' must be killed."""
        with pytest.raises(ValidationError):
            ModelResponse(
                provider=ProviderName.MINIMAX,
                model="m",
                parsed=None,
                rogue_field="evil",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m", parsed=None)
        with pytest.raises(ValidationError):
            resp.model = "mutated"  # type: ignore[misc]

    def test_validates_on_assignment(self) -> None:
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m", parsed=None)
        with pytest.raises(ValidationError):
            resp.requires_repair = "not a bool"  # type: ignore[assignment]

    def test_default_requires_repair_is_false(self) -> None:
        """Mutation requires_repair: bool = False -> True must be killed."""
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.requires_repair is False

    def test_default_files_changed_is_empty_tuple(self) -> None:
        """Mutation files_changed default must be empty (not e.g. None)."""
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.files_changed == ()

    def test_default_parsed_is_none(self) -> None:
        """Mutation parsed: ... = None -> "" must be killed (Pydantic rejects "")."""
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.parsed is None

    def test_default_raw_output_is_none(self) -> None:
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.raw_output is None

    def test_default_usage_is_none(self) -> None:
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.usage is None

    def test_default_error_is_none(self) -> None:
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.error is None

    def test_duration_s_default_is_zero(self) -> None:
        """Mutation duration_s = Field(default=0.0, ...) default must be 0.0."""
        resp = ModelResponse(provider=ProviderName.MINIMAX, model="m")
        assert resp.duration_s == 0.0

    def test_duration_s_rejects_negative(self) -> None:
        """Mutation Field(ge=0.0) on duration_s must be killed."""
        with pytest.raises(ValidationError):
            ModelResponse(
                provider=ProviderName.MINIMAX,
                model="m",
                parsed=None,
                duration_s=-0.1,
            )

    def test_rejects_unknown_provider_value(self) -> None:
        """Mutation on ProviderName enum must reject unknown values."""
        with pytest.raises(ValidationError):
            ModelResponse(
                provider="not-a-provider",  # type: ignore[arg-type]
                model="m",
                parsed=None,
            )


class TestModelUsageConfigKillers:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ModelUsage(input_tokens=1, output_tokens=1, rogue="evil")  # type: ignore[call-arg]

    def test_rejects_negative_input_tokens(self) -> None:
        """Mutation Field(ge=0) on input_tokens must be killed."""
        with pytest.raises(ValidationError):
            ModelUsage(input_tokens=-1, output_tokens=1)

    def test_rejects_negative_output_tokens(self) -> None:
        """Mutation Field(ge=0) on output_tokens must be killed."""
        with pytest.raises(ValidationError):
            ModelUsage(input_tokens=1, output_tokens=-1)

    def test_accepts_zero_input_tokens(self) -> None:
        """Mutation Field(ge=0) -> Field(ge=1) must be killed (zero must remain valid)."""
        u = ModelUsage(input_tokens=0, output_tokens=1)
        assert u.input_tokens == 0

    def test_accepts_zero_output_tokens(self) -> None:
        u = ModelUsage(input_tokens=1, output_tokens=0)
        assert u.output_tokens == 0

    def test_is_frozen(self) -> None:
        u = ModelUsage(input_tokens=1, output_tokens=1)
        with pytest.raises(ValidationError):
            u.input_tokens = 99  # type: ignore[misc]


class TestModelErrorConfigKillers:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ModelError(kind="timeout", message="x", rogue="evil")  # type: ignore[call-arg]

    def test_rejects_unknown_kind(self) -> None:
        """Mutation that widens the ErrorKind literal must be killed."""
        with pytest.raises(ValidationError):
            ModelError(kind="unknown-kind", message="x")  # type: ignore[arg-type]

    def test_default_retryable_is_false(self) -> None:
        """Mutation retryable: bool = False -> True must be killed."""
        err = ModelError(kind="timeout", message="x")
        assert err.retryable is False

    def test_default_original_error_is_none(self) -> None:
        """Mutation original_error: str | None = None -> "" must be killed."""
        rec = ModelRepair(outcome="not_needed", attempts=0)
        assert rec.original_error is None


class TestModelRepairConfigKillers:
    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ModelRepair(outcome="not_needed", attempts=0, rogue="evil")  # type: ignore[call-arg]

    def test_attempts_ge_zero(self) -> None:
        """Mutation Field(ge=0) on attempts must be killed."""
        with pytest.raises(ValidationError):
            ModelRepair(outcome="not_needed", attempts=-1)

    def test_attempts_le_one(self) -> None:
        """Mutation Field(le=1) on attempts must be killed — exactly ONE repair."""
        with pytest.raises(ValidationError):
            ModelRepair(outcome="repaired", attempts=2)

    def test_accepts_attempts_zero(self) -> None:
        """Mutation Field(ge=0) -> Field(ge=1) on attempts must be killed."""
        rec = ModelRepair(outcome="not_needed", attempts=0)
        assert rec.attempts == 0
