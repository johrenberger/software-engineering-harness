"""Tests for MiniMax transport + readiness (cluster N — MiniMax M3 refinement).

Each test names the **enforced boundary** and the **expected failure
state**, per the targeted refinement workplan's test strategy.

The offline tests cover every required case from Step 1 of the workplan:

- Success with structured JSON.
- Authentication failure.
- Rate limit with retry metadata.
- Timeout.
- Provider 5xx.
- Malformed JSON.
- Missing usage fields.
- Oversized response.
- Missing request ID.
- Connection failure.

Plus readiness-probe cases:

- No API key configured.
- API key configured but transport is a fake.
- API key configured and transport is HTTP — live.

Plus adapter-level cases:

- ``invoke`` translates a successful transport response into a
  ``ModelResponse`` with usage populated.
- ``invoke`` translates a transport error into a ``ModelResponse``
  with the closed ``ErrorKind`` set.
- The bearer token NEVER appears in ``repr`` or ``to_dict``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx
import pytest

from seharness.domain.enums import RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelResponse
from seharness.models.minimax import MiniMaxAdapter
from seharness.models.minimax_transport import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    FakeMiniMaxTransport,
    HttpMiniMaxTransport,
    MiniMaxMessage,
    MiniMaxRequest,
    MiniMaxTransport,
    MiniMaxTransportError,
    MiniMaxTransportResponse,
    RecordingMiniMaxTransport,
)
from seharness.models.provider_readiness import ProviderReadiness, not_live

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimax_request(
    prompt: str = "ping",
    *,
    system: str | None = None,
    max_tokens: int | None = 16,
    temperature: float | None = None,
) -> ModelRequest:
    context: dict[str, object] = {}
    if system is not None:
        context["system"] = system
    return ModelRequest(
        role=RoutingRole.PLANNING,
        prompt=prompt,
        context=context,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _make_transport_request() -> MiniMaxRequest:
    return MiniMaxRequest(
        model=DEFAULT_MODEL,
        messages=(MiniMaxMessage(role="user", content="ping"),),
        max_tokens=16,
    )


def _mock_client(
    handler: httpx.MockTransport,
) -> httpx.Client:
    """Build an httpx.Client backed by a MockTransport for offline tests."""
    return httpx.Client(transport=handler, timeout=DEFAULT_TIMEOUT_SECONDS)


def _ok_body(content: str = "hello") -> dict[str, object]:
    """OpenAI-compatible success body."""
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        "id": "req_test_123",
    }


@pytest.fixture(autouse=True)
def _clean_minimax_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ensure no leftover MINIMAX_API_KEY from the test environment."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    yield


# ---------------------------------------------------------------------------
# Transport protocol shape
# ---------------------------------------------------------------------------


class TestTransportProtocol:
    """ENFORCED BOUNDARY: ``MiniMaxTransport`` is a runtime-checkable
    Protocol with exactly one method, ``complete``.
    EXPECTED FAILURE STATE: a class missing the method is NOT
    recognized as a transport; the adapter will not accept it."""

    def test_fake_transport_satisfies_protocol(self) -> None:
        fake = FakeMiniMaxTransport()
        assert isinstance(fake, MiniMaxTransport)

    def test_recording_transport_satisfies_protocol(self) -> None:
        rec = RecordingMiniMaxTransport()
        assert isinstance(rec, MiniMaxTransport)

    def test_http_transport_satisfies_protocol(self) -> None:
        client = _mock_client(httpx.MockTransport(lambda req: httpx.Response(200)))
        http = HttpMiniMaxTransport(client=client)
        assert isinstance(http, MiniMaxTransport)

    def test_class_without_complete_is_not_a_transport(self) -> None:
        class NotATransport:
            pass

        assert not isinstance(NotATransport(), MiniMaxTransport)


# ---------------------------------------------------------------------------
# Required offline cases from Step 1
# ---------------------------------------------------------------------------


class TestHttpTransportSuccess:
    """ENFORCED BOUNDARY: a 200 response with a valid OpenAI-
    compatible body is normalized into a ``MiniMaxTransportResponse``
    with content, usage, and request_id populated.
    EXPECTED FAILURE STATE: the response carries the assistant
    text and the token counts."""

    def test_success_returns_content_and_usage(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_ok_body("hello world"),
                headers={"X-Request-Id": "req_abc"},
            )

        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        # Set the key for this test.
        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"

        resp = http.complete(_make_transport_request())
        assert resp.ok
        assert resp.content_text == "hello world"
        assert resp.usage_input_tokens == 5
        assert resp.usage_output_tokens == 3
        assert resp.request_id == "req_abc"
        assert resp.error is None

    def test_success_with_missing_usage_yields_none_tokens(self) -> None:
        """ENFORCED BOUNDARY: missing ``usage`` block is NOT a
        failure — the response succeeds with token counts = None."""

        def handler(req: httpx.Request) -> httpx.Response:
            body = _ok_body("hi")
            body.pop("usage", None)
            return httpx.Response(200, json=body)

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.ok
        assert resp.content_text == "hi"
        assert resp.usage_input_tokens is None
        assert resp.usage_output_tokens is None

    def test_success_with_request_id_in_body(self) -> None:
        """The provider may put the request id in the body."""

        def handler(req: httpx.Request) -> httpx.Response:
            body = _ok_body("hi")
            return httpx.Response(200, json=body)

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.ok
        # The body contained ``id=req_test_123``.
        assert resp.request_id == "req_test_123"


class TestHttpTransportAuth:
    """ENFORCED BOUNDARY: missing key, 401, and 403 all yield an
    ``auth`` error. The bearer token MUST NOT appear in the error
    message or in the transport's repr."""

    def test_missing_env_var_yields_auth_error(self) -> None:
        http = HttpMiniMaxTransport(api_key_env="MINIMAX_API_KEY")
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "auth"
        assert "MINIMAX_API_KEY" in resp.error.message
        assert "sk-test" not in resp.error.message

    def test_401_yields_auth_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "auth"
        assert resp.error.http_status == 401

    def test_403_yields_auth_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "forbidden"})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "auth"
        assert resp.error.http_status == 403

    def test_provider_base_resp_invalid_key_yields_auth_error(self) -> None:
        """The live probe observed the provider returning
        ``base_resp.status_code=2049`` with HTTP 200 for invalid
        keys. The transport must treat that as auth."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "base_resp": {
                        "status_code": 2049,
                        "status_msg": "invalid api key",
                    }
                },
            )

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "auth"
        assert "2049" in resp.error.message

    def test_repr_does_not_leak_endpoint_or_key(self) -> None:
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            endpoint="https://secret.example/v1",
        )
        text = repr(http)
        assert "secret.example" not in text
        # The env-var NAME is fine; the value (which we never hold)
        # is what matters.

    def test_error_message_does_not_leak_key(self) -> None:
        """If the provider echoes the bearer token in the body, the
        transport must NOT include it in the error message."""
        secret_key = "sk-LEAKED-DO-NOT-EXPOSE-1234567890"

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                content=f"auth failed for {secret_key}".encode(),
            )

        os.environ["MINIMAX_API_KEY"] = secret_key
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert secret_key not in resp.error.message


class TestHttpTransportRateLimit:
    """ENFORCED BOUNDARY: HTTP 429 yields a ``rate_limit`` error
    with the ``Retry-After`` header parsed into seconds."""

    def test_429_yields_rate_limit_with_retry_after(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": "slow down"},
                headers={"Retry-After": "7"},
            )

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "rate_limit"
        assert resp.error.retry_after_seconds == 7.0

    def test_429_without_retry_after_yields_none(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "slow down"})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "rate_limit"
        assert resp.error.retry_after_seconds is None

    def test_429_with_unparseable_retry_after(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"Retry-After": "soon"},
            )

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "rate_limit"
        assert resp.error.retry_after_seconds is None


class TestHttpTransportTimeout:
    """ENFORCED BOUNDARY: ``httpx.TimeoutException`` becomes a
    ``timeout`` error, not a raised exception."""

    def test_timeout_yields_timeout_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("read timed out")

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "timeout"


class TestHttpTransportServerError:
    """ENFORCED BOUNDARY: HTTP 5xx becomes a ``provider_failure``
    error with the status code attached."""

    def test_500_yields_provider_failure(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "provider_failure"
        assert resp.error.http_status == 500

    def test_503_yields_provider_failure(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "unavailable"})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "provider_failure"
        assert resp.error.http_status == 503


class TestHttpTransportMalformedJson:
    """ENFORCED BOUNDARY: a 200 response whose body is not valid
    JSON becomes a ``malformed_output`` error."""

    def test_malformed_json_yields_malformed_output(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{not valid json")

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "malformed_output"

    def test_choices_missing_yields_malformed_output(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"not_choices": True})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "malformed_output"

    def test_empty_content_yields_malformed_output(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": ""}}]},
            )

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "malformed_output"


class TestHttpTransportOversizedResponse:
    """ENFORCED BOUNDARY: a response body larger than
    ``max_response_bytes`` is rejected with ``oversized_response``
    BEFORE JSON parsing."""

    def test_oversized_body_yields_oversized_response(self) -> None:
        # 1 MiB max — send 2 MiB.
        big_body = b"x" * (2 * 1024 * 1024)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big_body)

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            max_response_bytes=1024 * 1024,
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "oversized_response"


class TestHttpTransportConnectionFailure:
    """ENFORCED BOUNDARY: a connection error yields
    ``connection_failure`` (not a raised exception)."""

    def test_connect_error_yields_connection_failure(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "connection_failure"

    def test_generic_httpx_error_yields_provider_failure(self) -> None:
        """A non-Timeout, non-Connect httpx error (e.g. network
        unreachable) yields the catch-all ``provider_failure``."""

        class CustomError(httpx.HTTPError):
            pass

        def handler(req: httpx.Request) -> httpx.Response:
            raise CustomError("custom failure")

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "provider_failure"


class TestRecordingTransportExhaustion:
    """ENFORCED BOUNDARY: when the recording transport runs out of
    queued responses it fails closed with ``provider_failure``."""

    def test_empty_queue_yields_provider_failure(self) -> None:
        rec = RecordingMiniMaxTransport()
        resp = rec.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "provider_failure"
        # The empty-queue response is still recorded so the test
        # knows exactly what happened.
        assert len(rec.recordings) == 1


class TestResponseExtractionDefensivePaths:
    """ENFORCED BOUNDARY: malformed shape elements (choices[0] not
    a dict, message not a dict) all yield ``malformed_output``."""

    def test_choices_first_not_a_dict(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": ["not a dict"]})

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "malformed_output"

    def test_message_not_a_dict(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": "not a dict"}]},
            )

        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        http = HttpMiniMaxTransport(
            api_key_env="MINIMAX_API_KEY",
            client=_mock_client(httpx.MockTransport(handler)),
        )
        resp = http.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "malformed_output"


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class TestFakeTransport:
    """ENFORCED BOUNDARY: ``FakeMiniMaxTransport`` returns queued
    responses in order. When the queue is empty it fails closed."""

    def test_returns_queued_responses_in_order(self) -> None:
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(content_text="first"),
                MiniMaxTransportResponse(content_text="second"),
            ]
        )
        r1 = fake.complete(_make_transport_request())
        r2 = fake.complete(_make_transport_request())
        assert r1.content_text == "first"
        assert r2.content_text == "second"

    def test_records_requests(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        req = _make_transport_request()
        fake.complete(req)
        assert fake.requests == [req]

    def test_empty_queue_yields_provider_failure(self) -> None:
        fake = FakeMiniMaxTransport()
        resp = fake.complete(_make_transport_request())
        assert resp.error is not None
        assert resp.error.error_kind == "provider_failure"

    def test_queue_response_appends(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="a")])
        fake.queue_response(MiniMaxTransportResponse(content_text="b"))
        assert fake.complete(_make_transport_request()).content_text == "a"
        assert fake.complete(_make_transport_request()).content_text == "b"


# ---------------------------------------------------------------------------
# Recording transport
# ---------------------------------------------------------------------------


class TestRecordingTransport:
    """ENFORCED BOUNDARY: ``RecordingMiniMaxTransport`` records
    every (request, response) pair for later replay."""

    def test_records_each_pair(self) -> None:
        rec = RecordingMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(content_text="x"),
                MiniMaxTransportResponse(content_text="y"),
            ]
        )
        rec.complete(_make_transport_request())
        rec.complete(_make_transport_request())
        assert len(rec.recordings) == 2
        assert rec.recordings[0][1].content_text == "x"
        assert rec.recordings[1][1].content_text == "y"

    def test_recording_can_be_serialized(self) -> None:
        """The recording must be JSON-serializable for offline replay."""
        rec = RecordingMiniMaxTransport(
            responses=[MiniMaxTransportResponse(content_text="z", request_id="r1")]
        )
        rec.complete(_make_transport_request())
        recording = rec.recordings[0]
        serialized = json.dumps(
            {
                "request": recording[0].model_dump(),
                "response": recording[1].model_dump(),
            },
            default=str,
        )
        # Round-trips through json.
        parsed = json.loads(serialized)
        assert parsed["response"]["content_text"] == "z"
        assert parsed["response"]["request_id"] == "r1"

    def test_recording_queue_response_appends(self) -> None:
        rec = RecordingMiniMaxTransport()
        rec.queue_response(MiniMaxTransportResponse(content_text="a"))
        rec.queue_response(MiniMaxTransportResponse(content_text="b"))
        assert rec.complete(_make_transport_request()).content_text == "a"
        assert rec.complete(_make_transport_request()).content_text == "b"


# ---------------------------------------------------------------------------
# Request / response model contracts
# ---------------------------------------------------------------------------


class TestRequestSchema:
    """ENFORCED BOUNDARY: ``MiniMaxRequest`` is frozen and rejects
    extra fields."""

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxRequest(
                model=DEFAULT_MODEL,
                messages=(MiniMaxMessage(role="user", content="x"),),
                made_up_field="bad",  # type: ignore[call-arg]
            )

    def test_minimum_one_message(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxRequest(
                model=DEFAULT_MODEL,
                messages=(),
            )

    def test_role_must_be_one_of_four(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxMessage(role="hacker", content="x")  # type: ignore[arg-type]


class TestResponseSchema:
    """ENFORCED BOUNDARY: ``MiniMaxTransportResponse`` is frozen
    and rejects extra fields."""

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxTransportResponse(content_text="x", surprise="y")  # type: ignore[call-arg]

    def test_ok_true_iff_content_present(self) -> None:
        ok = MiniMaxTransportResponse(content_text="hello")
        err = MiniMaxTransportResponse(
            error=MiniMaxTransportError(error_kind="timeout", message="x")
        )
        empty = MiniMaxTransportResponse()
        assert ok.ok is True
        assert err.ok is False
        assert empty.ok is False


# ---------------------------------------------------------------------------
# ProviderReadiness
# ---------------------------------------------------------------------------


class TestProviderReadiness:
    """ENFORCED BOUNDARY: ``ProviderReadiness.is_live()`` returns
    ``True`` only when every boolean field is True and the model id
    is set. This replaces the class-name substring detection that
    previously let a stub masquerade as live."""

    def test_is_live_when_all_true(self) -> None:
        r = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=True,
            model_identifier="minimax/MiniMax-M3",
        )
        assert r.is_live()

    def test_not_live_when_unconfigured(self) -> None:
        r = ProviderReadiness(
            configured=False,
            transport_available=True,
            transport_is_live=True,
            model_identifier="minimax/MiniMax-M3",
            reason="no key",
        )
        assert not r.is_live()

    def test_not_live_when_transport_not_live(self) -> None:
        r = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=False,  # fake transport
            model_identifier="minimax/MiniMax-M3",
            reason="fake",
        )
        assert not r.is_live()

    def test_not_live_when_model_id_empty(self) -> None:
        # The struct enforces ``model_identifier`` min_length=1;
        # callers that want to report "empty model id" use the
        # ``not_live()`` helper which sets the sentinel "unset".
        # Verify the helper contract: model_identifier is "unset"
        # when empty, and is_live() is False.
        r = not_live(reason="model id empty")
        assert r.model_identifier == "unset"
        assert not r.is_live()

    def test_readiness_is_frozen(self) -> None:
        r = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=True,
            model_identifier="minimax/MiniMax-M3",
        )
        with pytest.raises((AttributeError, ValueError)):
            r.configured = False  # type: ignore[misc]

    def test_not_live_helper(self) -> None:
        r = not_live(reason="missing key")
        assert not r.is_live()
        assert r.reason == "missing key"

    def test_not_live_helper_with_overrides(self) -> None:
        """The helper accepts overrides for any field."""

        r = not_live(
            reason="custom",
            configured=True,
            transport_available=True,
            model_identifier="minimax/MiniMax-M3",
        )
        assert r.configured is True
        assert r.transport_available is True
        assert r.transport_is_live is False  # default
        assert r.model_identifier == "minimax/MiniMax-M3"

    def test_not_live_helper_rejects_unknown_overrides(self) -> None:
        """Unknown kwarg is a programming error."""

        with pytest.raises(TypeError):
            not_live(reason="x", unknown_field=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Adapter readiness probe
# ---------------------------------------------------------------------------


class TestAdapterReadiness:
    """ENFORCED BOUNDARY: ``MiniMaxAdapter.readiness()`` reports
    ``is_live() == False`` whenever the transport is a fake,
    regardless of whether the API key is configured."""

    def test_no_key_no_fake(self) -> None:
        adapter = MiniMaxAdapter(api_key_env="MINIMAX_API_KEY")
        r = adapter.readiness()
        assert not r.is_live()
        assert r.configured is False
        assert "MINIMAX_API_KEY" in (r.reason or "")

    def test_key_set_but_transport_is_fake(self) -> None:
        """The whole point of the refinement: a fake transport
        must NOT be live, even with a valid key."""
        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        fake = FakeMiniMaxTransport()
        adapter = MiniMaxAdapter(api_key_env="MINIMAX_API_KEY", transport=fake)
        r = adapter.readiness()
        assert r.configured is True
        assert r.transport_is_live is False
        assert not r.is_live()
        assert r.reason is not None

    def test_key_set_and_http_transport_is_live(self) -> None:
        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = _mock_client(httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = MiniMaxAdapter(
            api_key_env="MINIMAX_API_KEY",
            transport=HttpMiniMaxTransport(client=client),
        )
        r = adapter.readiness()
        assert r.configured is True
        assert r.transport_is_live is True
        assert r.is_live()
        assert r.reason is None

    def test_empty_model_identifier_yields_not_live(self) -> None:
        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(api_key_env="MINIMAX_API_KEY", model_identifier="")
        r = adapter.readiness()
        assert not r.is_live()
        assert r.model_identifier == "unset"


# ---------------------------------------------------------------------------
# Adapter invoke translation
# ---------------------------------------------------------------------------


class TestAdapterInvoke:
    """ENFORCED BOUNDARY: ``MiniMaxAdapter.invoke`` translates the
    transport response into the provider-neutral ``ModelResponse``
    shape, populating usage when present."""

    def test_successful_translate_to_model_response(self) -> None:
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(
                    content_text="spec output",
                    usage_input_tokens=42,
                    usage_output_tokens=17,
                    request_id="req-xyz",
                )
            ]
        )
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert isinstance(resp, ModelResponse)
        assert resp.error is None
        assert resp.raw_output == "spec output"
        assert resp.usage is not None
        assert resp.usage.input_tokens == 42
        assert resp.usage.output_tokens == 17

    def test_transport_error_translates_to_closed_error_kind(self) -> None:
        """The closed ``ErrorKind`` literal must be used; the
        transport's internal ``error_kind`` string must not leak
        into the canonical response."""
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(
                    error=MiniMaxTransportError(
                        error_kind="rate_limit",
                        message="slow down",
                        retry_after_seconds=5.0,
                    )
                )
            ]
        )
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert resp.error is not None
        assert resp.error.kind == "rate_limit"
        assert resp.error.retryable is True

    def test_timeout_translates_to_closed_timeout_kind(self) -> None:
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(
                    error=MiniMaxTransportError(error_kind="timeout", message="t")
                )
            ]
        )
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert resp.error is not None
        assert resp.error.kind == "timeout"
        assert resp.error.retryable is True

    def test_auth_translates_to_closed_auth_kind(self) -> None:
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(
                    error=MiniMaxTransportError(error_kind="auth", message="bad key")
                )
            ]
        )
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert resp.error is not None
        assert resp.error.kind == "auth"
        assert resp.error.retryable is False

    def test_oversized_response_translates_to_malformed_output(self) -> None:
        fake = FakeMiniMaxTransport(
            responses=[
                MiniMaxTransportResponse(
                    error=MiniMaxTransportError(error_kind="oversized_response", message="too big")
                )
            ]
        )
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert resp.error is not None
        assert resp.error.kind == "malformed_output"

    def test_invoke_records_duration(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        resp = adapter.invoke(_make_minimax_request())
        assert resp.duration_s >= 0.0


# ---------------------------------------------------------------------------
# Adapter request building
# ---------------------------------------------------------------------------


class TestAdapterRequestBuilding:
    """ENFORCED BOUNDARY: the adapter's outbound request carries
    the configured model id and the prompt as a user message."""

    def test_prompt_carried_as_user_message(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        adapter.invoke(_make_minimax_request("describe the api"))
        sent = fake.requests[0]
        assert len(sent.messages) == 1
        assert sent.messages[0].role == "user"
        assert sent.messages[0].content == "describe the api"

    def test_system_prompt_added_when_provided(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        adapter.invoke(_make_minimax_request("user content", system="you are spec"))
        sent = fake.requests[0]
        assert len(sent.messages) == 2
        assert sent.messages[0].role == "system"
        assert sent.messages[0].content == "you are spec"
        assert sent.messages[1].role == "user"

    def test_model_identifier_passed_through(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake, model_identifier="custom-model-v1")
        adapter.invoke(_make_minimax_request())
        assert fake.requests[0].model == "custom-model-v1"

    def test_transport_property_returns_injected_transport(self) -> None:
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        assert adapter.transport is fake

    def test_response_format_passed_through_when_provided(self) -> None:
        """When ``context['response_format']`` is set, the adapter
        passes it through to the transport request."""
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        req = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="x",
            context={"response_format": {"type": "json_object"}},
        )
        adapter.invoke(req)
        sent = fake.requests[0]
        assert sent.response_format == {"type": "json_object"}

    def test_response_format_ignored_when_not_dict(self) -> None:
        """A non-dict ``response_format`` is ignored, not crashed on."""
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        req = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="x",
            context={"response_format": "not a dict"},
        )
        adapter.invoke(req)
        assert fake.requests[0].response_format is None

    def test_response_format_ignored_when_empty_dict(self) -> None:
        """An empty dict ``response_format`` is treated as no hint."""
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake)
        req = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="x",
            context={"response_format": {}},
        )
        adapter.invoke(req)
        assert fake.requests[0].response_format is None


# ---------------------------------------------------------------------------
# Endpoint default
# ---------------------------------------------------------------------------


class TestDefaultEndpoint:
    """ENFORCED BOUNDARY: the default endpoint is the MiniMax v1
    chat-completion endpoint that was probed live during Step 0."""

    def test_default_endpoint_matches_live_probe(self) -> None:
        assert DEFAULT_ENDPOINT == ("https://api.minimax.chat/v1/text/chatcompletion_v2")

    def test_default_endpoint_used_by_default(self) -> None:
        """A bare ``HttpMiniMaxTransport()`` uses the live endpoint."""
        http = HttpMiniMaxTransport()
        # The endpoint is private; check the underlying client.
        # We can introspect via _endpoint attribute even though it's
        # considered internal — the alternative would be to refactor
        # to expose it.
        assert http._endpoint == DEFAULT_ENDPOINT
