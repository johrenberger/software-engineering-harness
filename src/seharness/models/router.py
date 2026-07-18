"""ModelRouter — selects an adapter per workflow role with fallback (slice 4).

Per SPEC §10:

    Default routing:
        Planning: MiniMax
        Implementation: Codex
        Remediation: Codex
        Review: MiniMax
        Delivery packaging: MiniMax

    Fallback:
        MiniMax -> Codex
        Codex   -> MiniMax

    Switch models on:
        - provider unavailable
        - timeout
        - invalid structured output after one repair attempt
        - repeated task failure after the primary model's retry budget

    Do NOT switch models for the first deterministic validation defect.
"""

from __future__ import annotations

from collections.abc import Mapping

from seharness.domain.enums import (
    ProviderName,
    RoutingRole,
)
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelError, ModelResponse
from seharness.models.base import ModelAdapter
from seharness.models.output_repair import (
    ReattemptCallable,
    StructuredOutputRepair,
)

# Canonical default routing table per SPEC §10. Module-level so mypy can
# infer the precise enum value type without a runtime cast.
DEFAULT_ROUTING: Mapping[RoutingRole, ProviderName] = {
    RoutingRole.PLANNING: ProviderName.MINIMAX,
    RoutingRole.IMPLEMENTATION: ProviderName.CODEX,
    RoutingRole.REMEDIATION: ProviderName.CODEX,
    RoutingRole.REVIEW: ProviderName.MINIMAX,
    RoutingRole.DELIVERY: ProviderName.MINIMAX,
}


# Canonical default fallback table per SPEC §10.
DEFAULT_FALLBACK: Mapping[ProviderName, ProviderName] = {
    ProviderName.MINIMAX: ProviderName.CODEX,
    ProviderName.CODEX: ProviderName.MINIMAX,
}


class ModelRouter:
    """Selects the right adapter per routing role and applies fallback."""

    def __init__(
        self,
        *,
        adapters: Mapping[ProviderName, ModelAdapter],
        routing: Mapping[RoutingRole, ProviderName] | None = None,
        fallback_table: Mapping[ProviderName, ProviderName] | None = None,
        repair: StructuredOutputRepair | None = None,
    ) -> None:
        self._adapters: dict[ProviderName, ModelAdapter] = dict(adapters)
        self._routing: dict[RoutingRole, ProviderName] = (
            dict(routing) if routing is not None else dict(DEFAULT_ROUTING)
        )
        self._fallback: dict[ProviderName, ProviderName] = (
            dict(fallback_table) if fallback_table is not None else dict(DEFAULT_FALLBACK)
        )
        self._repair = repair or StructuredOutputRepair()

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Dispatch request to the routed adapter; fall back on canonical
        failure kinds; attempt one structured-output repair on malformed.

        Per SPEC: do NOT fall back on the first deterministic validation
        defect — only on the canonical failure kinds:
            - provider_failure
            - timeout
            - malformed_output (after ONE repair attempt)

        The router performs the single repair attempt itself, then if it
        fails, switches provider. It does NOT retry within the same
        provider — that is the caller's job (per the retry budgets in
        ``ExecutionConfig``).
        """
        primary = self._select_primary(request.role)
        primary_adapter = self._adapter_for(primary)

        primary_response = primary_adapter.invoke(request)

        # If the primary succeeded, return immediately.
        if primary_response.error is None:
            return primary_response

        # If the primary failed with a non-routable error kind, return it.
        if not self._is_routable_failure(primary_response.error):
            return primary_response

        # If the failure is malformed_output, attempt ONE repair on the
        # same adapter. After the repair, fall back if still failing.
        if primary_response.error.kind == "malformed_output":
            repaired = self._repair.maybe_repair(
                primary_response,
                reattempt=primary_adapter.invoke,
                request=request,
            )
            if repaired.outcome.value == "repaired":
                return repaired.response

        # Fall back to the configured alternate provider.
        fallback_provider = self._fallback.get(primary)
        if fallback_provider is None or fallback_provider == primary:
            return primary_response

        fallback_adapter = self._adapter_for(fallback_provider)
        return fallback_adapter.invoke(request)

    # ----- helpers --------------------------------------------------------

    def _select_primary(self, role: RoutingRole) -> ProviderName:
        provider = self._routing.get(role)
        if provider is None:
            msg = f"no routing configured for role: {role!r}"
            raise KeyError(msg)
        return provider

    def _adapter_for(self, provider: ProviderName) -> ModelAdapter:
        adapter = self._adapters.get(provider)
        if adapter is None:
            msg = f"no adapter registered for provider: {provider!r}"
            raise KeyError(msg)
        return adapter

    @staticmethod
    def _is_routable_failure(error: ModelError) -> bool:
        """Decide whether a canonical error kind should trigger fallback.

        Per SPEC §10:

        - provider_failure → fallback
        - timeout         → fallback
        - malformed_output → fallback (after one repair — handled by caller)
        - auth            → do NOT fallback silently (caller policy)
        """
        return error.kind in {"provider_failure", "timeout", "malformed_output"}


__all__ = [
    "DEFAULT_FALLBACK",
    "DEFAULT_ROUTING",
    "ModelRouter",
]


# Exported here to keep re-exports of the public API in one place.
_ = ReattemptCallable  # silence unused-import linters
