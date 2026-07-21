"""MiniMax adapter boundary (cluster N — MiniMax M3 refinement).

The :class:`MiniMaxAdapter` is the seam between the harness and the
MiniMax provider. It owns:

- The configured transport (:class:`MiniMaxTransport`).
- A capability-based readiness check (:class:`ProviderReadiness`).
- Translation between provider-neutral :class:`ModelRequest` and
  the transport's :class:`MiniMaxRequest`.
- Normalization of the transport's :class:`MiniMaxTransportResponse`
  into the canonical :class:`ModelResponse` shape.

The transport is INJECTED. Production wires :class:`HttpMiniMaxTransport`;
unit tests wire :class:`FakeMiniMaxTransport` or
:class:`RecordingMiniMaxTransport`. The default constructor builds
the production HTTP transport when an API key is configured, and a
not-live readiness struct otherwise.

The adapter NEVER raises on transport-level failures — every error
becomes a :class:`ModelResponse` with ``error`` populated. Workflow
code routes on the closed ``ErrorKind`` literal.

Historical note: this module was created in cluster A (slice 4) as
a stub that declared ``kind = LIVE`` and returned a fail-closed
``provider_failure`` for every call. That stub misled the production
profile into accepting a nonfunctional adapter as live. The cluster
N refinement replaces the stub with a real transport + capability-
based readiness so the production profile can no longer be lied to.
"""

from __future__ import annotations

import os
import time
from typing import Any

from seharness.domain.enums import ProviderKind, ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import (
    ErrorKind,
    ModelError,
    ModelResponse,
    ModelUsage,
)
from seharness.models.base import ModelAdapter
from seharness.models.minimax_transport import (
    DEFAULT_MODEL,
    FakeMiniMaxTransport,
    HttpMiniMaxTransport,
    MiniMaxMessage,
    MiniMaxRequest,
    MiniMaxTransport,
    MiniMaxTransportResponse,
)
from seharness.models.provider_readiness import ProviderReadiness, not_live

#: Mapping from transport error kind to the closed ``ErrorKind``
#: literal in :mod:`seharness.domain.results`. Kept here so the
#: adapter is the single source of truth for the mapping.
_TRANSPORT_ERROR_KIND_MAP: dict[str, ErrorKind] = {
    "timeout": "timeout",
    "auth": "auth",
    "rate_limit": "rate_limit",
    "provider_failure": "provider_failure",
    "malformed_output": "malformed_output",
    "oversized_response": "malformed_output",
    "connection_failure": "provider_failure",
}


class MiniMaxAdapter(ModelAdapter):
    """Boundary for the MiniMax provider with a real HTTP transport.

    The ``transport`` constructor parameter is the seam. The
    adapter inspects the transport at construction time to decide
    whether it is live: ``HttpMiniMaxTransport`` is live; the
    fakes are not. The ``kind`` attribute is therefore NO LONGER
    a hard-coded class attribute — it is computed from the
    transport and stored on the instance.

    Backward compatibility: ``kind`` is still a class attribute
    defaulting to :data:`ProviderKind.LIVE` so existing code that
    reads ``MiniMaxAdapter.kind`` still compiles. The
    :meth:`readiness` method is the authoritative source.
    """

    provider: ProviderName = ProviderName.MINIMAX
    # ``kind`` is the legacy class-level declaration. Per the
    # refinement workplan we do NOT trust this — callers must
    # use ``readiness().is_live()`` instead. We keep the default
    # to ``LIVE`` only so existing ``isinstance`` checks compile;
    # the production profile uses ``readiness()`` to gate startup.
    kind: ProviderKind = ProviderKind.LIVE

    def __init__(
        self,
        *,
        api_key_env: str = "MINIMAX_API_KEY",
        endpoint: str | None = None,
        model_identifier: str = DEFAULT_MODEL,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        transport: MiniMaxTransport | None = None,
    ) -> None:
        self._api_key_env = api_key_env
        self._endpoint = endpoint
        self._model_identifier = model_identifier
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)

        if transport is not None:
            self._transport: MiniMaxTransport = transport
        else:
            # Default: build the production HTTP transport. The
            # readiness probe below decides whether the adapter
            # is actually live.
            self._transport = HttpMiniMaxTransport(
                api_key_env=api_key_env,
                endpoint=endpoint or "https://api.minimax.chat/v1/text/chatcompletion_v2",
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
            )

        # Compute readiness once. The adapter is immutable in this
        # respect; if the env changes after construction, callers
        # must rebuild the adapter.
        self._readiness = self._probe_readiness()

    @property
    def model_identifier(self) -> str:
        return self._model_identifier

    @property
    def transport(self) -> MiniMaxTransport:
        return self._transport

    def readiness(self) -> ProviderReadiness:
        """Return the capability-based readiness struct.

        Production startup validates this struct via
        :meth:`ProviderReadiness.is_live`. The struct is built
        once at construction time and is immutable thereafter.
        """
        return self._readiness

    def _probe_readiness(self) -> ProviderReadiness:
        """Build the readiness struct for the configured transport.

        ``transport_is_live`` is ``True`` only for the production
        HTTP transport. The fakes report ``False`` so a
        ``FakeMiniMaxTransport`` cannot masquerade as live.
        """
        api_key = os.environ.get(self._api_key_env)
        configured = bool(api_key)
        transport_is_live = isinstance(self._transport, HttpMiniMaxTransport)

        if not configured:
            return not_live(
                reason=(f"environment variable {self._api_key_env!r} is unset"),
                model_identifier=self._model_identifier,
            )

        if not self._model_identifier:
            return not_live(
                reason="model_identifier is empty",
                configured=True,
                transport_is_live=transport_is_live,
            )

        if not transport_is_live:
            return ProviderReadiness(
                configured=configured,
                transport_available=True,
                transport_is_live=False,
                model_identifier=self._model_identifier,
                reason="transport is not the production HTTP transport",
            )

        return ProviderReadiness(
            configured=configured,
            transport_available=True,
            transport_is_live=True,
            model_identifier=self._model_identifier,
            reason=None,
        )

    def _build_provider_request(self, request: ModelRequest) -> MiniMaxRequest:
        """Translate a provider-neutral request into a transport request."""
        # The adapter composes a single user message carrying the
        # prompt. System messages can be added in a future refinement;
        # for cluster N the prompt carries the full phase input.
        messages: list[MiniMaxMessage] = []
        system = request.context.get("system") if isinstance(request.context, dict) else None
        if isinstance(system, str) and system:
            messages.append(MiniMaxMessage(role="system", content=system))
        messages.append(MiniMaxMessage(role="user", content=request.prompt))

        return MiniMaxRequest(
            model=self._model_identifier,
            messages=tuple(messages),
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            response_format=_maybe_response_format(request.context),
        )

    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Run the model and return a provider-neutral response.

        Per :meth:`ModelAdapter.invoke`'s contract, this method MUST
        NOT raise on adapter-level failures. Every error becomes a
        :class:`ModelResponse` with ``error`` populated.
        """
        started = time.monotonic()
        provider_request = self._build_provider_request(request)
        transport_response: MiniMaxTransportResponse = self._transport.complete(provider_request)
        duration_s = time.monotonic() - started

        if transport_response.error is not None:
            error_kind = _TRANSPORT_ERROR_KIND_MAP.get(
                transport_response.error.error_kind, "provider_failure"
            )
            return ModelResponse(
                provider=self.provider,
                model=self._model_identifier,
                parsed=None,
                error=ModelError(
                    kind=error_kind,
                    message=transport_response.error.message,
                    retryable=error_kind in ("rate_limit", "timeout", "provider_failure"),
                ),
                requires_repair=False,
                duration_s=duration_s,
            )

        # Successful response. The raw_output is the text content;
        # parsed is left to the structured-output repair layer.
        return ModelResponse(
            provider=self.provider,
            model=self._model_identifier,
            parsed=None,
            raw_output=transport_response.content_text,
            usage=(
                ModelUsage(
                    input_tokens=transport_response.usage_input_tokens or 0,
                    output_tokens=transport_response.usage_output_tokens or 0,
                )
                if transport_response.usage_input_tokens is not None
                and transport_response.usage_output_tokens is not None
                else None
            ),
            requires_repair=False,
            duration_s=duration_s,
        )


def _maybe_response_format(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the structured-output hint when the context requests it."""
    if not isinstance(context, dict):
        return None
    response_format = context.get("response_format")
    if isinstance(response_format, dict) and response_format:
        return response_format
    return None


__all__ = ["FakeMiniMaxTransport", "MiniMaxAdapter"]
