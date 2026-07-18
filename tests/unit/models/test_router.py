"""RED tests for behavior 03: ModelRouter.

Per SPEC §10: ModelRouter selects the right adapter per workflow role and
falls back on the configured fallback provider when the primary fails
(provider unavailable / timeout / invalid structured output after one
repair attempt / repeated task failure).
"""

from __future__ import annotations

import pytest

from seharness.domain.enums import (
    ProviderKind,
    ProviderName,
    RoutingRole,
)
from seharness.models import (
    ModelError,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelUsage,
)


class _StubAdapter:
    """Minimal adapter stand-in used to verify routing only."""

    provider: ProviderName = ProviderName.MINIMAX
    kind: ProviderKind = ProviderKind.FAKE

    def __init__(
        self,
        provider: ProviderName,
        kind: ProviderKind = ProviderKind.FAKE,
    ) -> None:
        self.provider = provider
        self.kind = kind
        self.calls: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            provider=self.provider,
            model=f"{self.provider}-test",
            parsed={"echo": request.prompt},
            usage=ModelUsage(input_tokens=1, output_tokens=2),
            error=None,
            requires_repair=False,
        )


def _req(*, role: RoutingRole = RoutingRole.PLANNING, prompt: str = "x") -> ModelRequest:
    return ModelRequest(role=role, prompt=prompt)


class TestRouterDispatch:
    def test_router_uses_default_routing_table(self) -> None:
        """Default routing per SPEC §10:
        Planning: MiniMax, Implementation: Codex, Remediation: Codex,
        Review: MiniMax, Delivery: MiniMax
        """
        mini = _StubAdapter(ProviderName.MINIMAX)
        codex = _StubAdapter(ProviderName.CODEX)
        router = ModelRouter(
            adapters={
                ProviderName.MINIMAX: mini,
                ProviderName.CODEX: codex,
            }
        )
        # Planning -> MiniMax
        router.invoke(_req(role=RoutingRole.PLANNING))
        assert mini.calls and not codex.calls
        # Implementation -> Codex
        mini.calls.clear()
        codex.calls.clear()
        router.invoke(_req(role=RoutingRole.IMPLEMENTATION))
        assert codex.calls and not mini.calls
        # Remediation -> Codex
        mini.calls.clear()
        codex.calls.clear()
        router.invoke(_req(role=RoutingRole.REMEDIATION))
        assert codex.calls and not mini.calls
        # Review -> MiniMax
        mini.calls.clear()
        codex.calls.clear()
        router.invoke(_req(role=RoutingRole.REVIEW))
        assert mini.calls and not codex.calls
        # Delivery -> MiniMax
        mini.calls.clear()
        codex.calls.clear()
        router.invoke(_req(role=RoutingRole.DELIVERY))
        assert mini.calls and not codex.calls

    def test_router_uses_custom_routing(self) -> None:
        mini = _StubAdapter(ProviderName.MINIMAX)
        codex = _StubAdapter(ProviderName.CODEX)
        router = ModelRouter(
            adapters={
                ProviderName.MINIMAX: mini,
                ProviderName.CODEX: codex,
            },
            routing={
                RoutingRole.PLANNING: ProviderName.CODEX,
                RoutingRole.IMPLEMENTATION: ProviderName.MINIMAX,
                RoutingRole.REMEDIATION: ProviderName.CODEX,
                RoutingRole.REVIEW: ProviderName.MINIMAX,
                RoutingRole.DELIVERY: ProviderName.MINIMAX,
            },
        )
        router.invoke(_req(role=RoutingRole.PLANNING))
        assert codex.calls
        router.invoke(_req(role=RoutingRole.IMPLEMENTATION))
        assert mini.calls


class TestRouterFallback:
    def test_router_falls_back_on_provider_failure(self) -> None:
        """Per SPEC: switch models on provider unavailable / timeout / invalid
        structured output after one repair attempt / repeated task failure."""

        class _BoomAdapter(_StubAdapter):
            def invoke(self, request: ModelRequest) -> ModelResponse:
                self.calls.append(request)
                return ModelResponse(
                    provider=self.provider,
                    model=f"{self.provider}-test",
                    parsed=None,
                    error=ModelError(kind="provider_failure", message="down"),
                    requires_repair=False,
                )

        boom = _BoomAdapter(ProviderName.MINIMAX)
        fallback = _StubAdapter(ProviderName.CODEX)
        router = ModelRouter(
            adapters={
                ProviderName.MINIMAX: boom,
                ProviderName.CODEX: fallback,
            },
            routing={
                RoutingRole.PLANNING: ProviderName.MINIMAX,
                RoutingRole.IMPLEMENTATION: ProviderName.CODEX,
                RoutingRole.REMEDIATION: ProviderName.CODEX,
                RoutingRole.REVIEW: ProviderName.MINIMAX,
                RoutingRole.DELIVERY: ProviderName.MINIMAX,
            },
            fallback_table={
                ProviderName.MINIMAX: ProviderName.CODEX,
                ProviderName.CODEX: ProviderName.MINIMAX,
            },
        )
        resp = router.invoke(_req(role=RoutingRole.PLANNING))
        assert resp.error is None
        assert resp.provider == ProviderName.CODEX
        # The original (failing) adapter must have been called once.
        assert len(boom.calls) == 1
        # Fallback adapter must have been called once.
        assert len(fallback.calls) == 1

    def test_router_does_not_switch_on_first_validation_defect(self) -> None:
        """Per SPEC: 'Do not switch models for the first deterministic
        validation defect.' We model 'validation defect' as a successful
        response with requires_repair=False but parsed=None. The router
        must NOT trigger fallback on the first such response — it only
        triggers on the canonical failure kinds (provider_failure /
        timeout / repeated failure)."""

        class _EmptyAdapter(_StubAdapter):
            def invoke(self, request: ModelRequest) -> ModelResponse:
                self.calls.append(request)
                return ModelResponse(
                    provider=self.provider,
                    model=f"{self.provider}-test",
                    parsed=None,
                    error=None,
                    requires_repair=False,
                )

        empty = _EmptyAdapter(ProviderName.MINIMAX)
        fallback = _StubAdapter(ProviderName.CODEX)
        router = ModelRouter(
            adapters={
                ProviderName.MINIMAX: empty,
                ProviderName.CODEX: fallback,
            },
            routing={
                RoutingRole.PLANNING: ProviderName.MINIMAX,
                RoutingRole.IMPLEMENTATION: ProviderName.CODEX,
                RoutingRole.REMEDIATION: ProviderName.CODEX,
                RoutingRole.REVIEW: ProviderName.MINIMAX,
                RoutingRole.DELIVERY: ProviderName.MINIMAX,
            },
            fallback_table={
                ProviderName.MINIMAX: ProviderName.CODEX,
                ProviderName.CODEX: ProviderName.MINIMAX,
            },
        )
        resp = router.invoke(_req(role=RoutingRole.PLANNING))
        # Primary stays in charge.
        assert resp.provider == ProviderName.MINIMAX
        assert len(empty.calls) == 1
        assert not fallback.calls

    def test_router_raises_when_role_unrouted(self) -> None:
        mini = _StubAdapter(ProviderName.MINIMAX)
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: mini},
            routing={},  # empty
        )
        with pytest.raises(KeyError):
            router.invoke(_req(role=RoutingRole.PLANNING))

    def test_router_raises_when_adapter_missing(self) -> None:
        router = ModelRouter(
            adapters={},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
        )
        with pytest.raises(KeyError):
            router.invoke(_req(role=RoutingRole.PLANNING))


class TestRouterRecordedOutcome:
    def test_router_records_provider_used_in_response(self) -> None:
        mini = _StubAdapter(ProviderName.MINIMAX)
        router = ModelRouter(adapters={ProviderName.MINIMAX: mini})
        resp = router.invoke(_req(role=RoutingRole.PLANNING))
        assert resp.provider == ProviderName.MINIMAX
        assert resp.model == f"{ProviderName.MINIMAX}-test"
