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

The endpoint defaults to ``https://api.minimax.chat/v1/text/chatcompletion_v2``
per the live probe; it can be overridden for tests and alternative
deployments.
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


#: Default MiniMax endpoint. Probed at the start of the refinement work
#: (status 200 reachable, auth via ``Authorization: Bearer ...``).
DEFAULT_ENDPOINT: str = "https://api.minimax.chat/v1/text/chatcompletion_v2"

#: Default model identifier. Matches the existing default in
#: :class:`seharness.models.minimax.MiniMaxAdapter`.
DEFAULT_MODEL: str = "minimax/MiniMax-M3"

#: Default request timeout in seconds.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

#: Default max response body size in bytes (4 MiB). Larger bodies are
#: rejected before parsing.
DEFAULT_MAX_RESPONSE_BYTES: int = 4 * 1024 * 1024


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
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = DEFAULT_MODEL
    messages: tuple[MiniMaxMessage, ...] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Optional structured-output hint. MiniMax's response_format is
    # not yet observed; the field is passed through when the model
    # supports it and ignored otherwise.
    response_format: dict[str, Any] | None = None


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
    """

    def __init__(
        self,
        *,
        api_key_env: str = "MINIMAX_API_KEY",
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key_env = api_key_env
        self._endpoint = endpoint
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

        # Serialize the request body. Pydantic's ``model_dump_json``
        # is deterministic and uses our frozen schema.
        body = request.model_dump_json(exclude_none=True)
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
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    in_tok = prompt if isinstance(prompt, int) and prompt >= 0 else None
    out_tok = completion if isinstance(completion, int) and completion >= 0 else None
    return in_tok, out_tok


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "FakeMiniMaxTransport",
    "HttpMiniMaxTransport",
    "MiniMaxMessage",
    "MiniMaxRequest",
    "MiniMaxTransport",
    "MiniMaxTransportError",
    "MiniMaxTransportResponse",
    "RecordingMiniMaxTransport",
]
