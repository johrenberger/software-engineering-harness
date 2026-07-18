"""RED tests for behavior 01: Model contract.

Per SPEC §10: ModelAdapter is an ABC with `invoke(request: ModelRequest) -> ModelResponse`.
The contract must be provider-neutral — every adapter receives the same ModelRequest
shape and returns the same ModelResponse shape.
"""

from __future__ import annotations

import inspect
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from seharness.domain.enums import ProviderKind, ProviderName, RepairOutcome, RoutingRole
from seharness.models import ModelRequest, get_adapter
from seharness.models.base import ModelAdapter


class _ProbeAdapter(ModelAdapter):
    """Concrete subclass used to assert the ABC contract."""

    provider: ClassVar[ProviderName] = "minimax"
    kind: ClassVar[ProviderKind] = ProviderKind.LIVE
    last_request: ClassVar[object] = None

    def invoke(self, request: object) -> object:
        type(self).last_request = request
        # Returns a sentinel so we can verify shape pass-through.
        return {"echo": request}


class TestModelAdapterIsAbstract:
    def test_cannot_instantiate_abc_directly(self) -> None:
        """ModelAdapter must be abstract; direct instantiation must raise TypeError."""
        with pytest.raises(TypeError):
            ModelAdapter()  # type: ignore[abstract]

    def test_subclass_must_implement_invoke(self) -> None:
        """Subclasses that do not implement invoke() must fail to instantiate."""

        class IncompleteAdapter(ModelAdapter):
            pass

        with pytest.raises(TypeError):
            IncompleteAdapter()  # type: ignore[abstract]

    def test_subclass_with_invoke_is_instantiable(self) -> None:
        a = _ProbeAdapter()
        assert isinstance(a, ModelAdapter)


class TestModelAdapterSignature:
    def test_invoke_signature(self) -> None:
        sig = inspect.signature(ModelAdapter.invoke)
        # invoke(self, request) -> ModelResponse
        params = list(sig.parameters.values())
        assert len(params) == 2  # self + request
        assert params[0].name == "self"
        assert params[1].name == "request"

    def test_invoke_is_abstract(self) -> None:
        assert getattr(ModelAdapter.invoke, "__isabstractmethod__", False) is True

    def test_invoke_return_annotation_is_model_response(self) -> None:
        sig = inspect.signature(ModelAdapter.invoke)
        # return annotation string must reference ModelResponse
        ret = sig.return_annotation
        assert ret is not None
        # The annotation may be the ModelResponse class or a string.
        # Accept either form to remain robust to `from __future__ import annotations`.
        name = getattr(ret, "__name__", str(ret))
        assert "ModelResponse" in name, f"expected ModelResponse annotation, got {ret!r}"


class TestProviderNameStrEnum:
    def test_provider_name_values(self) -> None:
        # Per SPEC §10 default routing and §6, providers are minimax and codex.
        assert ProviderName.MINIMAX == "minimax"
        assert ProviderName.CODEX == "codex"

    def test_provider_name_is_strenum(self) -> None:
        for p in ProviderName:
            assert isinstance(p, str)

    def test_provider_name_members(self) -> None:
        assert set(ProviderName.__members__) >= {"MINIMAX", "CODEX"}


class TestProviderKindStrEnum:
    def test_provider_kind_values(self) -> None:
        # Per SPEC §10 the fake adapter is a real implementation but distinct
        # from "live" network-bound providers.
        assert ProviderKind.LIVE.value == "live"
        assert ProviderKind.FAKE.value == "fake"
        assert ProviderKind.LOCAL.value == "local"

    def test_provider_kind_is_strenum(self) -> None:
        for k in ProviderKind:
            assert isinstance(k, str)


class TestRoutingRoleStrEnum:
    def test_routing_role_values(self) -> None:
        # Per SPEC §10 default routing table.
        assert RoutingRole.PLANNING.value == "planning"
        assert RoutingRole.IMPLEMENTATION.value == "implementation"
        assert RoutingRole.REMEDIATION.value == "remediation"
        assert RoutingRole.REVIEW.value == "review"
        assert RoutingRole.DELIVERY.value == "delivery"


class TestRepairOutcomeStrEnum:
    def test_repair_outcome_values(self) -> None:
        # Per SPEC §10: ONE repair attempt on malformed structured output.
        assert RepairOutcome.REPAIRED.value == "repaired"
        assert RepairOutcome.REJECTED.value == "rejected"
        assert RepairOutcome.NOT_NEEDED.value == "not_needed"


class TestAdapterMetadataAttributes:
    """Every adapter must expose provider + kind class-level metadata."""

    def test_probe_adapter_has_provider(self) -> None:
        assert _ProbeAdapter.provider == "minimax"

    def test_probe_adapter_has_kind(self) -> None:
        assert _ProbeAdapter.kind == ProviderKind.LIVE


def _build_minimal_request() -> Any:
    """Helper to build a minimal model request via the package public API."""
    return ModelRequest(
        role=RoutingRole.PLANNING,
        prompt="hello",
    )


class TestModelRequestRoundTrip:
    def test_minimal_request_round_trip(self) -> None:
        req = _build_minimal_request()
        assert req.role == RoutingRole.PLANNING
        assert req.prompt == "hello"

    def test_request_rejects_unknown_role(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(role="not-a-role", prompt="x")  # type: ignore[arg-type]

    def test_request_rejects_missing_prompt(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequest(role=RoutingRole.PLANNING)  # type: ignore[call-arg]


class TestAdapterAcceptsModelRequest:
    def test_probe_adapter_invoked_with_request(self) -> None:
        a = _ProbeAdapter()
        req = ModelRequest(role=RoutingRole.PLANNING, prompt="hi")
        _ProbeAdapter.last_request = None
        result = a.invoke(req)
        assert _ProbeAdapter.last_request is req
        assert result == {"echo": req}


class TestModelAdapterRegistryProtocol:
    """The package must expose a way to look up an adapter by provider name."""

    def test_get_adapter_returns_subclass(self) -> None:
        cls = get_adapter("minimax")
        # Must be a class that is a subclass of ModelAdapter
        assert isinstance(cls, type)
        assert issubclass(cls, ModelAdapter)
