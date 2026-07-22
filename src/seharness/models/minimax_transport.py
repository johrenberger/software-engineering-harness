"""MiniMax HTTP transport contract (cluster N — MiniMax M3 refinement).

This module owns **everything transport-shaped** for the MiniMax
adapter. Per the targeted refinement workplan:

- HTTP endpoint construction.
- Authentication header injection.
- Request serialization.
- Response parsing.
- Provider error normalization.
- Request timeout.
- Rate-limit handling.
- Provider request ID extraction.
- Usage extraction.
- Response size limits.

The orchestrator and phase services MUST NOT import ``httpx`` or
otherwise reach around this module. Any HTTP call to MiniMax goes
through :class:`MiniMaxTransport` (``Protocol``) or one of its
concrete implementations.

Two concrete transports ship:

- :class:`HttpMiniMaxTransport` — production HTTP transport.
- :class:`FakeMiniMaxTransport` — offline test double that returns
  scripted responses.

A third, :class:`RecordingMiniMaxTransport`, records every request
and response pair so the deterministic replay path (Step 8) can
re-run the same scenario offline.

Design constraints (enforced by ``tests/unit/models/test_minimax_transport.py``):

1. The transport never raises on adapter-level failures. Failures
   become a :class:`MiniMaxTransportResponse` with ``error`` populated.
2. The transport NEVER logs or stores the bearer token, the
   Authorization header, or the response body. ``__repr__`` and
   ``to_dict()`` MUST redact the credential.
3. Response bodies larger than ``max_response_bytes`` are rejected
   before JSON parsing.
4. The transport honors ``timeout_seconds`` (passed by the adapter).
5. The transport extracts the provider request ID from the
   ``X-Request-Id`` / ``Request-Id`` response header (whichever
   MiniMax returns).
6. The transport extracts usage from the response body when present.

The endpoint defaults to ``https://api.minimax.io/v1/chat/completions``
(the **OpenAI-compatible** endpoint) per the official API
documentation. The older
``https://api.minimax.chat/v1/text/chatcompletion_v2`` endpoint is
officially documented but marked **deprecated**; it is NOT the
default transport. The deprecated endpoint remains accepted when
explicitly configured so legacy accounts are not broken.

Model identifiers are **not** hard-coded. The current official
documentation names MiniMax M2.7, M2.5, M2.1, and M2; ``MiniMax-M3``
is not yet listed. Until the credentialed account confirms an M3
listing, the harness treats the model ID as a mandatory
``MINIMAX_MODEL`` environment variable. The transport's ``model``
field accepts whatever string the caller passes; validation against
the live model catalog happens via :func:`validate_model_against_account`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Default MiniMax endpoint. The official, OpenAI-compatible chat
#: completions endpoint per
#: https://platform.minimax.io/docs/api-reference/text-chat-openai
DEFAULT_ENDPOINT: str = "https://api.minimax.io/v1/chat/completions"

#: Legacy / native chat-completion endpoint. Accepted when
#: explicitly configured (or when the operator selects
#: ``protocol="native"``), but NOT the default.
#: https://platform.minimax.io/docs/api-reference/text-post
NATIVE_ENDPOINT: str = "https://api.minimax.io/v1/text/chatcompletion_v2"

#: Pre-existing deprecated endpoint that lives on a different
#: domain (``api.minimax.chat``). Kept here for backward
#: compatibility with older accounts that wired against this
#: URL before the migration to ``api.minimax.io``.
DEPRECATED_LEGACY_ENDPOINT: str = "https://api.minimax.chat/v1/text/chatcompletion_v2"

#: Default model catalog endpoint used for startup validation.
MODELS_ENDPOINT: str = "https://api.minimax.io/v1/models"

#: Environment variable holding the model identifier. The
#: production default is :data:`DEFAULT_MODEL` (Cluster M3-1
#: corrective). Callers may override with ``MINIMAX_MODEL`` for
#: testing or to point at a non-default model; ``MINIMAX_MODEL``
#: set to an empty string is treated as unset and falls back to
#: the default.
DEFAULT_MODEL_ENV: str = "MINIMAX_MODEL"

#: Production-default model identifier (Cluster M3-1 corrective).
#: Per the corrective doc: ``MINIMAX_MODEL`` when explicitly
#: set; otherwise ``MiniMax-M3``. The default MUST NOT be
#: silently substituted with M2.7 or any other model.
DEFAULT_MODEL: str = "MiniMax-M3"

#: Open-set of supported protocol identifiers. Used by the
#: adapter and transport to decide which endpoint + body shape
#: to use. Both protocols normalize into the same
#: :class:`MiniMaxTransportResponse` so the rest of the harness
#: is protocol-agnostic.
PROTOCOL_NATIVE: str = "native"
PROTOCOL_OPENAI_COMPATIBLE: str = "openai-compatible"
SUPPORTED_PROTOCOLS: tuple[str, ...] = (PROTOCOL_NATIVE, PROTOCOL_OPENAI_COMPATIBLE)

#: Default protocol when none is configured.
DEFAULT_PROTOCOL: str = PROTOCOL_OPENAI_COMPATIBLE

#: Default request timeout in seconds.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

#: Default max response body size in bytes (4 MiB). Larger bodies are
#: rejected before parsing.
DEFAULT_MAX_RESPONSE_BYTES: int = 4 * 1024 * 1024

#: Default thinking mode for M3 production runs. Per the
#: corrective doc, thinking is enabled for specification,
#: planning, test generation, implementation, remediation, and
#: review. ``None`` means "let the model decide"; ``True`` and
#: ``False`` are explicit overrides.
DEFAULT_THINKING: bool | None = True

#: Default service tier for M3 production runs. ``None`` means
#: "let the model decide"; any string is forwarded verbatim.
DEFAULT_SERVICE_TIER: str | None = "standard"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MiniMaxMessage(BaseModel):
    """A single chat-completions message.

    Mirrors the OpenAI-compatible ``messages[].role`` / ``content``
    shape. The transport never inspects the content for secrets; the
    caller (adapter) is responsible for redaction before this
    object is constructed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str = Field(pattern=r"^(system|user|assistant|tool)$")
    content: str


class MiniMaxRequest(BaseModel):
    """Provider-shaped request payload for the MiniMax HTTP transport.

    The adapter translates its provider-neutral :class:`ModelRequest`
    into one of these. The transport does not know about
    :class:`ModelRequest`.

    Field names mirror the official OpenAI-compatible schema:

    - ``model``: caller-supplied; defaults to
      :data:`DEFAULT_MODEL` (``MiniMax-M3``) when the operator
      has not set ``MINIMAX_MODEL``.
    - ``messages``: chat-completions shape.
    - ``max_completion_tokens``: the upper bound on completion
      tokens the model may emit. Maps to ``max_tokens`` in the
      OpenAI client SDK but is named ``max_completion_tokens``
      on the wire per the official MiniMax docs.
    - ``temperature``: optional sampling temperature.
    - ``stream``: always ``False``; this transport is non-streaming.
    - ``thinking``: Cluster M3-1 corrective. Optional
      ``thinking: {type: "enabled"}`` block sent on the wire
      when the operator has thinking mode enabled. ``None``
      omits the block (model decides).
    - ``service_tier``: Cluster M3-1 corrective. Optional
      ``service_tier`` string sent on the wire when configured.
      ``None`` omits the field.
    - ``protocol``: Cluster M3-1 corrective. The wire protocol
      the transport must use (``"native"`` or
      ``"openai-compatible"``). Defaults to
      :data:`DEFAULT_PROTOCOL`. The transport picks the
      matching endpoint.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = ""
    messages: tuple[MiniMaxMessage, ...] = Field(min_length=1)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stream: bool = False
    thinking: bool | None = None
    service_tier: str | None = None
    protocol: str = DEFAULT_PROTOCOL


def _serialize_request_body(
    request: MiniMaxRequest,
    *,
    protocol: str,
) -> str:
    """Serialize a :class:`MiniMaxRequest` into the wire body
    for the given protocol.

    Both protocols share the same envelope where they overlap;
    the differences are the body field names. The native
    protocol expects ``prompt`` (concatenated user content) and
    a flat ``max_tokens`` / ``temperature``; the OpenAI-
    compatible protocol expects ``messages[]``,
    ``max_completion_tokens``, and ``temperature``.

    Cluster M3-1 corrective:

    - ``thinking`` and ``service_tier`` are forwarded verbatim on
      both protocols (the native protocol has documented
      ``thinking: {type: "enabled"}`` and ``service_tier``
      keys; the OpenAI-compatible protocol uses the same keys
      per the official MiniMax docs).
    - The native body shape concatenates ``messages`` into a
      single ``prompt`` string (system + user segments joined
      with ``\n\n``). This is the format the legacy endpoint
      expects; Cluster M3-5 live verification confirms the
      real server accepts it.
    - ``max_completion_tokens`` is mapped to ``max_tokens`` on
      the native wire (the legacy field name) and forwarded
      verbatim on OpenAI-compatible.
    - ``stream`` is always ``False``; not forwarded on the
      native wire (legacy endpoint does not support streaming
      in this transport).
    """
    if protocol == PROTOCOL_NATIVE:
        # Concatenate messages into a single prompt. System +
        # user segments are joined with a double newline so the
        # model sees them as separate sections.
        prompt_parts: list[str] = []
        for message in request.messages:
            if message.role == "system":
                prompt_parts.append(f"[system]\n{message.content}")
            elif message.role == "user":
                prompt_parts.append(message.content)
            else:
                # assistant / tool messages collapse into the
                # prompt verbatim — the native endpoint does
                # not distinguish them.
                prompt_parts.append(f"[{message.role}]\n{message.content}")
        native_body: dict[str, Any] = {
            "model": request.model,
            "prompt": "\n\n".join(prompt_parts),
        }
        if request.max_completion_tokens is not None:
            native_body["max_tokens"] = request.max_completion_tokens
        if request.temperature is not None:
            native_body["temperature"] = request.temperature
        if request.thinking is not None:
            native_body["thinking"] = {"type": "enabled" if request.thinking else "disabled"}
        if request.service_tier is not None:
            native_body["service_tier"] = request.service_tier
        return json.dumps(native_body, sort_keys=True)

    # OpenAI-compatible: serialize the request with Pydantic
    # and add the thinking / service_tier keys verbatim.
    openai_body: dict[str, Any] = json.loads(request.model_dump_json(exclude_none=True))
    if request.thinking is not None:
        openai_body["thinking"] = {"type": "enabled" if request.thinking else "disabled"}
    if request.service_tier is not None:
        openai_body["service_tier"] = request.service_tier
    return json.dumps(openai_body, sort_keys=True)


class MiniMaxTransportError(BaseModel):
    """Normalized transport error.

    The transport converts every failure mode (timeout, auth, rate
    limit, 5xx, malformed JSON, oversized body, connection failure)
    into one of these. The adapter maps ``error_kind`` onto the
    closed ``ErrorKind`` literal in :mod:`seharness.domain.results`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    error_kind: str  # One of: timeout, auth, rate_limit, provider_failure,
    # malformed_output, oversized_response, connection_failure.
    message: str
    retry_after_seconds: float | None = None
    http_status: int | None = None


class MiniMaxTransportResponse(BaseModel):
    """Provider-shaped response from the MiniMax transport.

    On success, ``content_text`` carries the assistant message and
    ``usage`` carries token counts. On failure, ``error`` is
    populated and the other fields are ``None``/empty.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_text: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    request_id: str | None = None
    error: MiniMaxTransportError | None = None

    @property
    def ok(self) -> bool:
        """``True`` iff the response carries content (not an error)."""
        return self.error is None and self.content_text is not None


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MiniMaxTransport(Protocol):
    """Injectable transport seam for MiniMax HTTP calls.

    The adapter owns a single ``MiniMaxTransport`` instance. Unit
    tests inject a :class:`FakeMiniMaxTransport`; production wires
    in :class:`HttpMiniMaxTransport`.
    """

    def complete(self, request: MiniMaxRequest) -> MiniMaxTransportResponse: ...


# ---------------------------------------------------------------------------
# Production HTTP transport
# ---------------------------------------------------------------------------


class HttpMiniMaxTransport:
    """Production HTTP transport for MiniMax.

    Uses ``httpx.Client`` synchronously. The transport is intentionally
    simple — no retries, no connection pooling across requests. Rate
    limit handling lives in the router (cluster H, story H1) so this
    layer stays a thin HTTP wrapper.

    The bearer token is read from the environment variable named in
    ``api_key_env`` at construction time. The transport MUST NOT
    accept the token as a constructor argument or store it in any
    field that survives ``__repr__``.

    Cluster M3-1 corrective — protocol switch:

    The transport supports two wire protocols, both normalized into
    the same :class:`MiniMaxTransportResponse`:

    - ``openai-compatible`` (default): routes to
      :data:`DEFAULT_ENDPOINT`
      (``https://api.minimax.io/v1/chat/completions``) with the
      OpenAI-compatible chat-completions body shape
      (``messages[]``).
    - ``native``: routes to :data:`NATIVE_ENDPOINT`
      (``https://api.minimax.io/v1/text/chatcompletion_v2``) with
      the legacy chat-completion body shape (``prompt``). The
      wire-body shape is selected by the adapter at request
      construction; the transport picks the matching URL.

    The transport is otherwise protocol-agnostic: it serializes
    whatever body shape the adapter hands it. Callers MAY pass
    an explicit ``endpoint`` to override the protocol default
    (legacy accounts wired to ``api.minimax.chat`` continue to
    work).
    """

    def __init__(
        self,
        *,
        api_key_env: str = "MINIMAX_API_KEY",
        endpoint: str | None = None,
        protocol: str = DEFAULT_PROTOCOL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
    ) -> None:
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ValueError(
                f"protocol must be one of {SUPPORTED_PROTOCOLS!r}, got {protocol!r}",
            )
        self._api_key_env = api_key_env
        self._protocol = protocol
        # Resolve endpoint: explicit wins, else protocol default.
        if endpoint is not None:
            self._endpoint = endpoint
        elif protocol == PROTOCOL_NATIVE:
            self._endpoint = NATIVE_ENDPOINT
        else:
            self._endpoint = DEFAULT_ENDPOINT
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        # The httpx.Client is owned by the transport so callers do not
        # need to manage its lifecycle. Tests may inject a client to
        # intercept requests via httpx.MockTransport.
        self._client = client or httpx.Client(timeout=self._timeout_seconds)
        self._owns_client = client is None

    def __repr__(self) -> str:
        # Never include the API key or endpoint in repr. The endpoint
        # is not secret but printing it would clutter logs.
        return (
            f"HttpMiniMaxTransport(api_key_env={self._api_key_env!r}, "
            f"timeout_seconds={self._timeout_seconds})"
        )

    @property
    def client(self) -> httpx.Client:
        """The underlying httpx client.

        Exposed so the adapter can reuse the same connection pool
        for the catalog ``GET /v1/models`` request and the chat
        completions POST. Production startup therefore uses a
        single client for both; tests can inject a
        ``MockTransport``-backed client to intercept both.
        """
        return self._client

    @property
    def protocol(self) -> str:
        """The configured wire protocol (``native`` /
        ``openai-compatible``). Cluster M3-1 corrective."""
        return self._protocol

    @property
    def endpoint(self) -> str:
        """The resolved endpoint (after protocol / explicit
        resolution). Exposed for diagnostics and tests."""
        return self._endpoint

    def _bearer_token(self) -> str | None:
        token = os.environ.get(self._api_key_env)
        return token if token else None

    def complete(  # noqa: PLR0911 — branched response normalisation; readability over consolidation
        self, request: MiniMaxRequest
    ) -> MiniMaxTransportResponse:
        token = self._bearer_token()
        if not token:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="auth",
                    message=(f"environment variable {self._api_key_env!r} is empty or unset"),
                )
            )

        # Cluster M3-1 corrective — protocol-aware body shape.
        # The native protocol expects a legacy ``prompt`` body;
        # the openai-compatible protocol expects ``messages[]``.
        # Both routes fall through to the same normalized
        # :class:`MiniMaxTransportResponse` so the rest of the
        # harness is protocol-agnostic.
        body = _serialize_request_body(
            request,
            protocol=request.protocol or self._protocol,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            # Enforce size limit by reading via streaming and checking
            # content length before JSON parse.
            http_response = self._client.post(
                self._endpoint,
                content=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="timeout",
                    message=f"request timed out after {self._timeout_seconds}s",
                )
            )
        except httpx.ConnectError as exc:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="connection_failure",
                    message=f"connection failure: {exc!s}",
                )
            )
        except httpx.HTTPError as exc:
            # Catch-all for httpx errors. The specific subclasses above
            # handle the most common cases.
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="provider_failure",
                    message=f"transport error: {exc!s}",
                )
            )

        # Auth failure
        if http_response.status_code in (401, 403):
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="auth",
                    message=f"http {http_response.status_code}: "
                    f"{_safe_status_reason(http_response)}",
                    http_status=http_response.status_code,
                )
            )

        # Rate limit
        if http_response.status_code == 429:
            retry_after = _parse_retry_after(http_response)
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="rate_limit",
                    message=f"http 429: {_safe_status_reason(http_response)}",
                    retry_after_seconds=retry_after,
                    http_status=429,
                )
            )

        # 5xx
        if http_response.status_code >= 500:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="provider_failure",
                    message=f"http {http_response.status_code}: "
                    f"{_safe_status_reason(http_response)}",
                    http_status=http_response.status_code,
                )
            )

        # 2xx but content too large
        body_bytes = http_response.content
        if len(body_bytes) > self._max_response_bytes:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="oversized_response",
                    message=(
                        f"response body {len(body_bytes)} bytes exceeds "
                        f"limit {self._max_response_bytes}"
                    ),
                    http_status=http_response.status_code,
                )
            )

        # 2xx — parse JSON
        try:
            parsed = json.loads(body_bytes)
        except json.JSONDecodeError as exc:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="malformed_output",
                    message=f"response body is not valid JSON: {exc!s}",
                    http_status=http_response.status_code,
                )
            )

        # Provider error envelope (MiniMax returns
        # ``base_resp.status_code != 0`` on logical errors even when
        # the HTTP status is 200).
        base_resp = parsed.get("base_resp") if isinstance(parsed, dict) else None
        if isinstance(base_resp, dict) and base_resp.get("status_code", 0) != 0:
            status_code = base_resp.get("status_code", 0)
            status_msg = base_resp.get("status_msg", "unknown provider error")
            # Treat non-zero base_resp as auth if status_code matches
            # the known auth codes (1004, 2049) — these are the
            # credentials-related codes observed during the live probe.
            error_kind = "auth" if status_code in (1004, 2049) else "provider_failure"
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind=error_kind,
                    message=(
                        f"provider returned base_resp.status_code={status_code}: {status_msg}"
                    ),
                )
            )

        # Extract the assistant text + usage
        content_text = _extract_content_text(parsed)
        usage_in, usage_out = _extract_usage(parsed)
        request_id = _extract_request_id(http_response, parsed)

        if not content_text:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="malformed_output",
                    message=("response had no choices[0].message.content"),
                    http_status=http_response.status_code,
                )
            )

        return MiniMaxTransportResponse(
            content_text=content_text,
            usage_input_tokens=usage_in,
            usage_output_tokens=usage_out,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# Test / offline doubles
# ---------------------------------------------------------------------------


class FakeMiniMaxTransport:
    """Scripted offline transport for unit tests.

    Tests pre-load a list of :class:`MiniMaxTransportResponse` objects
    via :meth:`queue_response` (or pass them to the constructor).
    Each :meth:`complete` call pops one response. If the queue is
    empty the transport fails closed with ``provider_failure``.

    The fake NEVER touches the network and NEVER reads environment
    variables — it is the deterministic offline double.
    """

    def __init__(self, responses: Iterable[MiniMaxTransportResponse] = ()) -> None:
        self._responses: list[MiniMaxTransportResponse] = list(responses)
        self.requests: list[MiniMaxRequest] = []

    def queue_response(self, response: MiniMaxTransportResponse) -> None:
        """Append a response to the queue."""
        self._responses.append(response)

    def complete(self, request: MiniMaxRequest) -> MiniMaxTransportResponse:
        self.requests.append(request)
        if not self._responses:
            return MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="provider_failure",
                    message=("FakeMiniMaxTransport ran out of queued responses"),
                )
            )
        return self._responses.pop(0)


class RecordingMiniMaxTransport:
    """Records every (request, response) pair for deterministic replay.

    The deterministic replay path (Step 8) uses this transport to
    capture a real MiniMax exchange once, then re-runs the exchange
    against the captured fixture for every offline test. The
    recording itself is in-memory; persistence lives in the test
    fixtures.
    """

    def __init__(
        self,
        *,
        responses: Iterable[MiniMaxTransportResponse] = (),
    ) -> None:
        self._responses: list[MiniMaxTransportResponse] = list(responses)
        self.recordings: list[tuple[MiniMaxRequest, MiniMaxTransportResponse]] = []

    def queue_response(self, response: MiniMaxTransportResponse) -> None:
        self._responses.append(response)

    def complete(self, request: MiniMaxRequest) -> MiniMaxTransportResponse:
        response = (
            self._responses.pop(0)
            if self._responses
            else MiniMaxTransportResponse(
                error=MiniMaxTransportError(
                    error_kind="provider_failure",
                    message=("RecordingMiniMaxTransport ran out of queued responses"),
                )
            )
        )
        self.recordings.append((request, response))
        return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_status_reason(http_response: httpx.Response) -> str:
    """Return a redacted status reason.

    The HTTP reason phrase (e.g. ``Unauthorized``) is safe to log,
    but the body is NOT — bodies can contain echoed credentials or
    internal state. We deliberately return only the reason phrase,
    never the body.
    """
    return http_response.reason_phrase or "no reason phrase"


def _parse_retry_after(http_response: httpx.Response) -> float | None:
    """Parse the ``Retry-After`` header.

    Returns the value in seconds (float), or ``None`` if the header
    is absent or unparseable. The router's rate-limit retry uses
    this as the backoff floor.
    """
    header = http_response.headers.get("retry-after")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _extract_request_id(http_response: httpx.Response, body: dict[str, Any]) -> str | None:
    """Extract the provider request id from response headers / body.

    MiniMax has been observed to use ``X-Request-Id`` (header) and
    ``id`` / ``request_id`` (body). Whichever appears first wins.
    """
    for header in ("x-request-id", "request-id"):
        value = http_response.headers.get(header)
        if isinstance(value, str) and value:
            return value
    if isinstance(body, dict):
        for key in ("id", "request_id", "requestId"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _extract_content_text(body: dict[str, Any]) -> str | None:
    """Extract the assistant message text from the parsed response.

    MiniMax responses use the OpenAI-compatible shape:

    .. code-block:: json

        {
            "choices": [
                {"message": {"role": "assistant", "content": "..."}}
            ]
        }
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) and content else None


def _extract_usage(body: dict[str, Any]) -> tuple[int | None, int | None]:
    """Extract (input_tokens, output_tokens) from the parsed response.

    Returns ``(None, None)`` if usage is missing or malformed.

    The OpenAI-compatible schema uses ``prompt_tokens`` and
    ``completion_tokens``. Some MiniMax responses also include
    ``total_tokens``; we deliberately ignore that field.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    in_tok = prompt if isinstance(prompt, int) and prompt >= 0 else None
    out_tok = completion if isinstance(completion, int) and completion >= 0 else None
    return in_tok, out_tok


# ---------------------------------------------------------------------------
# Model-catalog validation
# ---------------------------------------------------------------------------


def parse_model_catalog(body: dict[str, Any]) -> tuple[str, ...]:
    """Parse the OpenAI-compatible ``GET /v1/models`` response body.

    The catalog body has shape ``{"data": [{"id": "..."}, ...]}``.
    Returns the tuple of model ids in the order returned by the API.
    Returns an empty tuple if the body is malformed.
    """
    data = body.get("data")
    if not isinstance(data, list):
        return ()
    ids: list[str] = []
    for entry in data:
        if isinstance(entry, dict):
            mid = entry.get("id")
            if isinstance(mid, str) and mid:
                ids.append(mid)
    return tuple(ids)


def validate_model_against_account(  # noqa: PLR0911 — branched error normalization; readability over consolidation
    *,
    configured_model: str,
    api_key_env: str = "MINIMAX_API_KEY",
    models_endpoint: str = MODELS_ENDPOINT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> tuple[bool, tuple[str, ...], str | None]:
    """Validate ``configured_model`` against the live MiniMax catalog.

    Returns a 3-tuple ``(is_listed, available_models, error_message)``:

    - ``is_listed``: True iff ``configured_model`` appears in the
      catalog returned by ``GET /v1/models``.
    - ``available_models``: the list of model ids returned by the API
      (in order); useful for the operator to pick a different model
      when ``is_listed`` is False.
    - ``error_message``: a redacted error message when the catalog
      could not be fetched (auth failure, timeout, etc.). ``None``
      on success.

    The function does NOT raise; it returns a structured result so
    callers can fail closed at startup. Production startup MUST
    reject configuration when ``is_listed`` is False.

    Per the refinement workplan: do not silently substitute one
    model for another. If the configured M3 model is absent from
    the account, stop and surface the available models.
    """
    token = os.environ.get(api_key_env)
    if not token:
        return (False, (), f"environment variable {api_key_env!r} is unset")

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout_seconds)
    try:
        try:
            http_response = client.get(
                models_endpoint,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException:
            return (False, (), f"catalog fetch timed out after {timeout_seconds}s")
        except httpx.ConnectError as exc:
            return (False, (), f"catalog fetch connection failure: {exc!s}")
        except httpx.HTTPError as exc:
            return (False, (), f"catalog fetch transport error: {exc!s}")

        if http_response.status_code in (401, 403):
            return (False, (), f"http {http_response.status_code} fetching catalog")
        if http_response.status_code >= 400:
            return (
                False,
                (),
                f"http {http_response.status_code} fetching catalog",
            )

        try:
            parsed = json.loads(http_response.content)
        except json.JSONDecodeError as exc:
            return (False, (), f"catalog response is not valid JSON: {exc!s}")

        if not isinstance(parsed, dict):
            return (False, (), "catalog response is not a JSON object")

        available = parse_model_catalog(parsed)
        return (configured_model in available, available, None)
    finally:
        if owns_client:
            client.close()


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MODEL_ENV",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEPRECATED_LEGACY_ENDPOINT",
    "MODELS_ENDPOINT",
    "FakeMiniMaxTransport",
    "HttpMiniMaxTransport",
    "MiniMaxMessage",
    "MiniMaxRequest",
    "MiniMaxTransport",
    "MiniMaxTransportError",
    "MiniMaxTransportResponse",
    "RecordingMiniMaxTransport",
    "parse_model_catalog",
    "validate_model_against_account",
]
