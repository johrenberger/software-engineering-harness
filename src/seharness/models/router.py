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

import logging
import time
from collections.abc import Callable, Mapping

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

_LOG = logging.getLogger(__name__)


class RetryPolicy:
    """Bounded retry-with-backoff policy for transient rate-limit errors.

    Cluster H, story H1. The router consults this policy when a
    primary adapter returns a ``rate_limit`` ``ModelError``. The
    policy controls:

    - ``max_attempts``: total tries against the primary provider
      (1 means no retry; default 3 = up to 2 retries).
    - ``initial_backoff_s``: sleep before the first retry (default
      1.0s). Subsequent retries double the backoff.
    - ``max_backoff_s``: cap on the doubled backoff (default 30.0s).
    - ``sleeper``: function used to sleep (default ``time.sleep``).
      Override in tests for deterministic timing.

    The policy is bounded — retries always terminate. Rate limits
    that exhaust ``max_attempts`` trigger a fallback to the
    configured alternate provider per SPEC §10.
    """

    __slots__ = ("_initial_backoff_s", "_max_attempts", "_max_backoff_s", "_sleeper")

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        initial_backoff_s: float = 1.0,
        max_backoff_s: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {max_attempts}"
            raise ValueError(msg)
        if initial_backoff_s < 0:
            msg = f"initial_backoff_s must be >= 0, got {initial_backoff_s}"
            raise ValueError(msg)
        if max_backoff_s < initial_backoff_s:
            msg = (
                f"max_backoff_s ({max_backoff_s}) must be >= "
                f"initial_backoff_s ({initial_backoff_s})"
            )
            raise ValueError(msg)
        self._max_attempts = max_attempts
        self._initial_backoff_s = float(initial_backoff_s)
        self._max_backoff_s = float(max_backoff_s)
        self._sleeper = sleeper

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def backoff_seconds(self, attempt_index: int) -> float:
        """Return the sleep duration before retry number ``attempt_index``.

        ``attempt_index`` is 0-based: 0 is the first retry, 1 the
        second, etc. Backoff doubles with each attempt, capped at
        ``max_backoff_s``.
        """
        if attempt_index < 0:
            msg = f"attempt_index must be >= 0, got {attempt_index}"
            raise ValueError(msg)
        backoff: float = self._initial_backoff_s * (2**attempt_index)
        return min(backoff, self._max_backoff_s)

    def sleep(self, attempt_index: int) -> None:
        """Sleep for the backoff duration before retry number ``attempt_index``."""
        self._sleeper(self.backoff_seconds(attempt_index))

    def __repr__(self) -> str:
        return (
            f"RetryPolicy(max_attempts={self._max_attempts}, "
            f"initial_backoff_s={self._initial_backoff_s}, "
            f"max_backoff_s={self._max_backoff_s})"
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
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._adapters: dict[ProviderName, ModelAdapter] = dict(adapters)
        self._routing: dict[RoutingRole, ProviderName] = (
            dict(routing) if routing is not None else dict(DEFAULT_ROUTING)
        )
        self._fallback: dict[ProviderName, ProviderName] = (
            dict(fallback_table) if fallback_table is not None else dict(DEFAULT_FALLBACK)
        )
        self._repair = repair or StructuredOutputRepair()
        self._retry_policy = retry_policy or RetryPolicy()

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

        # Rate-limit retry path (cluster H, story H1). If the primary
        # is rate-limited, retry with bounded exponential backoff
        # before considering fallback. Non-rate-limit failures skip
        # the retry path entirely.
        primary_response = self._invoke_with_rate_limit_retry(
            primary_adapter, request
        )

        # If the primary succeeded (after retries), return immediately.
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

    def _invoke_with_rate_limit_retry(
        self,
        adapter: ModelAdapter,
        request: ModelRequest,
    ) -> ModelResponse:
        """Invoke ``adapter`` with bounded retry on ``rate_limit`` errors.

        Cluster H, story H1. Behaviour:

        - First attempt: invoke once. If the response is not a
          ``rate_limit`` error, return it.
        - Subsequent attempts: up to ``retry_policy.max_attempts - 1``
          retries, sleeping ``retry_policy.backoff_seconds(i)``
          before attempt ``i+1``. Backoff doubles each retry, capped
          at ``retry_policy.max_backoff_s``.
        - On retry exhaustion, return the last ``rate_limit`` error
          so the caller can decide to fall back.

        The retry loop is *bounded* — it always terminates.
        """
        response = adapter.invoke(request)
        if response.error is None or response.error.kind != "rate_limit":
            return response

        max_attempts = self._retry_policy.max_attempts
        for attempt_index in range(max_attempts - 1):
            backoff = self._retry_policy.backoff_seconds(attempt_index)
            _LOG.info(
                "ModelRouter: rate_limit from %s; retry %d/%d after %.2fs",
                adapter.provider,
                attempt_index + 1,
                max_attempts - 1,
                backoff,
            )
            self._retry_policy.sleep(attempt_index)
            response = adapter.invoke(request)
            if response.error is None or response.error.kind != "rate_limit":
                return response

        return response

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

        Per SPEC §10 (extended by cluster H, story H1):

        - provider_failure → fallback
        - timeout         → fallback
        - malformed_output → fallback (after one repair — handled by caller)
        - rate_limit      → retry-then-fallback (handled by caller)
        - auth            → do NOT fallback silently (caller policy)
        """
        return error.kind in {
            "provider_failure",
            "timeout",
            "malformed_output",
            "rate_limit",
        }


__all__ = [
    "DEFAULT_FALLBACK",
    "DEFAULT_ROUTING",
    "ModelRouter",
    "RetryPolicy",
]


# Exported here to keep re-exports of the public API in one place.
_ = ReattemptCallable  # silence unused-import linters
