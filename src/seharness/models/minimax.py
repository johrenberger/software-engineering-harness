"""MiniMax adapter boundary (slice 4).

Slice 4 ships the contract and the no-network boundary. Per user
decision (B): the real adapter implementation lands in a later slice
(slice 8 per the §28 build sequence). This module provides the
``MiniMaxAdapter`` class that:

- Inherits ``ModelAdapter`` and so satisfies the type contract.
- Sets ``provider = MINIMAX`` and ``kind = LIVE`` so the router can
  discover it.
- Fails closed in ``invoke`` — it returns a normalized
  ``provider_failure`` ModelError so callers never see a transport
  exception leaking out of the boundary.

When the real HTTP client lands in a later slice, only the body of
``invoke`` changes.
"""

from __future__ import annotations

import os

from seharness.domain.enums import ProviderKind, ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelError, ModelResponse
from seharness.models.base import ModelAdapter


class MiniMaxAdapter(ModelAdapter):
    """Boundary for the MiniMax provider.

    Slice 4 ships this class so the router/contract tests can register it.
    The HTTP transport is intentionally absent — the adapter fails closed
    in ``invoke`` until a later slice wires it up.
    """

    provider: ProviderName = ProviderName.MINIMAX
    kind: ProviderKind = ProviderKind.LIVE

    def __init__(
        self,
        *,
        api_key_env: str = "MINIMAX_API_KEY",
        endpoint: str | None = None,
        model_identifier: str = "minimax/MiniMax-M3",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key_env = api_key_env
        self._endpoint = endpoint
        self._model_identifier = model_identifier
        self._timeout_seconds = float(timeout_seconds)

    @property
    def model_identifier(self) -> str:
        return self._model_identifier

    def invoke(self, request: ModelRequest) -> ModelResponse:
        # Slice 4 boundary: fail closed with a normalized provider_failure.
        # Real HTTP wiring lands in a later slice.
        has_key = bool(os.environ.get(self._api_key_env))
        msg = (
            "MiniMaxAdapter HTTP transport is not yet implemented in slice 4; "
            f"api_key_env={self._api_key_env!r} configured={'yes' if has_key else 'no'}"
        )
        return ModelResponse(
            provider=self.provider,
            model=self._model_identifier,
            parsed=None,
            error=ModelError(kind="provider_failure", message=msg),
            requires_repair=False,
        )


__all__ = ["MiniMaxAdapter"]
