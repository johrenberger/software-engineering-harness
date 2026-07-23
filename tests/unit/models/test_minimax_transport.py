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
    DEFAULT_TIMEOUT_SECONDS,
    DEPRECATED_LEGACY_ENDPOINT,
    MODELS_ENDPOINT,
    NATIVE_ENDPOINT,
    FakeMiniMaxTransport,
    HttpMiniMaxTransport,
    MiniMaxMessage,
    MiniMaxRequest,
    MiniMaxTransport,
    MiniMaxTransportError,
    MiniMaxTransportResponse,
    RecordingMiniMaxTransport,
    parse_model_catalog,
    validate_model_against_account,
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
        model="minimax/MiniMax-M2.7",  # placeholder; tests don't use it
        messages=(MiniMaxMessage(role="user", content="ping"),),
        max_completion_tokens=16,
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
    """Ensure no leftover MINIMAX_API_KEY or MINIMAX_MODEL from the test environment."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
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
                model="minimax/MiniMax-M2.7",
                messages=(MiniMaxMessage(role="user", content="x"),),
                made_up_field="bad",  # type: ignore[call-arg]
            )

    def test_minimum_one_message(self) -> None:
        with pytest.raises(ValueError):
            MiniMaxRequest(
                model="minimax/MiniMax-M2.7",
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
            model_identifier="minimax/MiniMax-M2.7",
        )
        assert r.is_live()

    def test_not_live_when_unconfigured(self) -> None:
        r = ProviderReadiness(
            configured=False,
            transport_available=True,
            transport_is_live=True,
            model_identifier="minimax/MiniMax-M2.7",
            reason="no key",
        )
        assert not r.is_live()

    def test_not_live_when_transport_not_live(self) -> None:
        r = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=False,  # fake transport
            model_identifier="minimax/MiniMax-M2.7",
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
            model_identifier="minimax/MiniMax-M2.7",
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
            model_identifier="minimax/MiniMax-M2.7",
        )
        assert r.configured is True
        assert r.transport_available is True
        assert r.transport_is_live is False  # default
        assert r.model_identifier == "minimax/MiniMax-M2.7"

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
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
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
        assert r.model_identifier == "MiniMax-M2.7"

    def test_empty_model_identifier_falls_back_to_default(self) -> None:
        """Cluster M3-1: ``model_identifier=""`` is treated as
        "unset" so the adapter falls back to the M3 default.
        A fake transport is the gate that fails the readiness
        check; the model identifier surfaces as ``MiniMax-M3``
        on the readiness struct.
        """
        import os

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(
            api_key_env="MINIMAX_API_KEY",
            model_identifier="",
            transport=FakeMiniMaxTransport(),
        )
        r = adapter.readiness()
        assert not r.is_live()
        assert r.model_identifier == "MiniMax-M3"


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

    def test_max_completion_tokens_passed_through(self) -> None:
        """``ModelRequest.max_tokens`` maps to
        ``MiniMaxRequest.max_completion_tokens`` (the wire name
        per the official docs)."""
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake, model_identifier="minimax/MiniMax-M2.7")
        req = ModelRequest(role=RoutingRole.PLANNING, prompt="x", max_tokens=128)
        adapter.invoke(req)
        assert fake.requests[0].max_completion_tokens == 128

    def test_response_format_never_set(self) -> None:
        """Per the refinement workplan, ``response_format`` is NOT
        used in cluster N — JSON output is requested through the
        prompt and validated locally with Pydantic. The transport
        request must never carry a ``response_format`` field."""
        fake = FakeMiniMaxTransport(responses=[MiniMaxTransportResponse(content_text="ok")])
        adapter = MiniMaxAdapter(transport=fake, model_identifier="minimax/MiniMax-M2.7")
        req = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="x",
            context={"response_format": {"type": "json_object"}},
        )
        adapter.invoke(req)
        # ``MiniMaxRequest`` no longer has a ``response_format``
        # field; the call would have raised during pydantic
        # construction if it were set. Confirm the field name is
        # absent in the serialized body.
        sent = fake.requests[0]
        assert not hasattr(sent, "response_format")


# ---------------------------------------------------------------------------
# Endpoint contract
# ---------------------------------------------------------------------------


class TestEndpointContract:
    """ENFORCED BOUNDARY: the default endpoint is the official
    OpenAI-compatible chat-completions endpoint
    ``https://api.minimax.io/v1/chat/completions``. The legacy
    ``/v1/text/chatcompletion_v2`` endpoint is deprecated and MUST
    NOT be the default."""

    def test_default_endpoint_is_openai_compatible(self) -> None:
        assert DEFAULT_ENDPOINT == "https://api.minimax.io/v1/chat/completions"

    def test_legacy_endpoint_is_deprecated(self) -> None:
        """The legacy endpoint is documented but officially
        deprecated; it remains accepted as an explicit override
        but is not the default."""
        assert DEPRECATED_LEGACY_ENDPOINT == ("https://api.minimax.chat/v1/text/chatcompletion_v2")
        assert DEPRECATED_LEGACY_ENDPOINT != DEFAULT_ENDPOINT

    def test_default_endpoint_used_by_default(self) -> None:
        """A bare ``HttpMiniMaxTransport()`` uses the OpenAI-
        compatible endpoint."""
        http = HttpMiniMaxTransport()
        assert http._endpoint == DEFAULT_ENDPOINT

    def test_models_endpoint_constant(self) -> None:
        """The catalog endpoint is the OpenAI-compatible
        ``/v1/models`` (per the workplan's
        ``GET /v1/models`` startup-validation rule)."""
        assert MODELS_ENDPOINT == "https://api.minimax.io/v1/models"


# ---------------------------------------------------------------------------
# Model-catalog validation
# ---------------------------------------------------------------------------


class TestParseModelCatalog:
    """ENFORCED BOUNDARY: ``parse_model_catalog`` returns a tuple
    of model ids from an OpenAI-compatible ``GET /v1/models``
    response, in order. Malformed inputs return an empty tuple."""

    def test_parses_well_formed_catalog(self) -> None:
        body = {
            "data": [
                {"id": "MiniMax-M2.7"},
                {"id": "MiniMax-M2.5"},
                {"id": "MiniMax-M2.1"},
                {"id": "MiniMax-M2"},
            ]
        }
        assert parse_model_catalog(body) == (
            "MiniMax-M2.7",
            "MiniMax-M2.5",
            "MiniMax-M2.1",
            "MiniMax-M2",
        )

    def test_empty_catalog(self) -> None:
        assert parse_model_catalog({"data": []}) == ()

    def test_missing_data_field(self) -> None:
        assert parse_model_catalog({}) == ()

    def test_data_not_a_list(self) -> None:
        assert parse_model_catalog({"data": "not a list"}) == ()

    def test_body_data_none(self) -> None:
        """``data`` explicitly set to None is treated as empty."""

        assert parse_model_catalog({"data": None}) == ()

    def test_skips_entries_without_id(self) -> None:
        body = {
            "data": [
                {"id": "MiniMax-M2.7"},
                {"name": "no id here"},
                {"id": ""},
                {"id": 42},
            ]
        }
        assert parse_model_catalog(body) == ("MiniMax-M2.7",)


class TestValidateModelAgainstAccount:
    """ENFORCED BOUNDARY: ``validate_model_against_account``
    returns ``(is_listed, available_models, error_message)``.
    Production startup MUST fail closed when ``is_listed`` is
    False; the harness MUST NOT silently substitute one model
    for another."""

    def test_listed_model_returns_true(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.method == "GET"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "MiniMax-M2.7"},
                        {"id": "MiniMax-M2.5"},
                    ]
                },
            )

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is True
        assert available == ("MiniMax-M2.7", "MiniMax-M2.5")
        assert err is None

    def test_unlisted_model_returns_false(self) -> None:
        """When the configured model is NOT in the live catalog,
        the function returns ``(False, available, None)``. The
        caller is expected to refuse startup."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"id": "MiniMax-M2.7"}]},
            )

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, available, err = validate_model_against_account(
            configured_model="MiniMax-M3",  # not in catalog
            client=client,
        )
        assert is_listed is False
        assert available == ("MiniMax-M2.7",)
        assert err is None

    def test_missing_api_key_returns_error(self) -> None:
        is_listed, available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
        )
        assert is_listed is False
        assert available == ()
        assert "MINIMAX_API_KEY" in (err or "")

    def test_401_returns_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, _available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert err is not None
        assert "401" in err

    def test_500_returns_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, _available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert err is not None
        assert "500" in err

    def test_timeout_returns_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("read timed out")

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, _available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert err is not None
        assert "timed out" in err

    def test_malformed_json_returns_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{not json")

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, _available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert err is not None
        assert "JSON" in err

    def test_request_url_matches_models_endpoint(self) -> None:
        """The catalog GET hits the configured models endpoint."""
        seen_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen_urls.append(str(req.url))
            return httpx.Response(200, json={"data": []})

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert seen_urls == ["https://api.minimax.io/v1/models"]

    def test_authorization_header_carries_bearer(self) -> None:
        """The catalog GET uses the same bearer-token auth as the
        chat-completions endpoint."""

        def handler(req: httpx.Request) -> httpx.Response:
            auth = req.headers.get("Authorization")
            assert auth == "Bearer sk-test"
            return httpx.Response(200, json={"data": [{"id": "MiniMax-M2.7"}]})

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, _, _ = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is True

    def test_does_not_silently_substitute_unlisted_model(self) -> None:
        """Per the workplan: if the configured model is absent from
        the account, stop and surface the available models; do not
        silently substitute one. The function returns ``False``
        plus the actual available list — substitution is the
        caller's choice (and the caller should refuse to start)."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"id": "MiniMax-M2.5"}, {"id": "MiniMax-M2"}]},
            )

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, available, _ = validate_model_against_account(
            configured_model="MiniMax-M3",
            client=client,
        )
        assert is_listed is False
        # The available list is returned in full so the operator
        # can pick deliberately; it is NOT a substitution.
        assert available == ("MiniMax-M2.5", "MiniMax-M2")

    def test_creates_internal_client_when_none_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no ``client`` is passed, the helper builds its own
        ``httpx.Client``. The internal client is closed after the
        call (``owns_client=True`` branch).
        """

        from unittest.mock import patch

        # Spy on httpx.Client to confirm it's constructed + closed.
        with patch("seharness.models.minimax_transport.httpx.Client") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.get.return_value = httpx.Response(
                200, json={"data": [{"id": "MiniMax-M2.7"}]}
            )
            monkeypatch.setenv("MINIMAX_API_KEY", "sk-test")
            is_listed, available, err = validate_model_against_account(
                configured_model="MiniMax-M2.7",
            )
            assert is_listed is True
            assert available == ("MiniMax-M2.7",)
            assert err is None
            # The helper constructed a client (no client was passed).
            mock_client_cls.assert_called_once()
            # And closed it after the call.
            mock_client.close.assert_called_once()

    def test_handles_catalog_body_not_a_dict(self) -> None:
        """When the body parses as JSON but is not an object, the
        helper returns ``(False, (), "not a JSON object")``."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "a", "dict"])

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert available == ()
        assert err is not None
        assert "JSON object" in err

    def test_handles_catalog_generic_httpx_error(self) -> None:
        """A non-Timeout non-Connect ``httpx.HTTPError`` is caught
        by the generic catch-all."""

        class CustomError(httpx.HTTPError):
            pass

        def handler(req: httpx.Request) -> httpx.Response:
            raise CustomError("custom catalog failure")

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        is_listed, available, err = validate_model_against_account(
            configured_model="MiniMax-M2.7",
            client=client,
        )
        assert is_listed is False
        assert available == ()
        assert err is not None
        assert "transport error" in err


# ---------------------------------------------------------------------------
# Adapter-required model identifier
# ---------------------------------------------------------------------------


class TestRequiredModelIdentifier:
    """ENFORCED BOUNDARY: per the corrective doc, the model ID
    is configurable and the production default is
    ``MiniMax-M3`` (not M2.7). The constructor resolves the
    model id in this order:

    1. Explicit ``model_identifier`` argument (non-empty after
       stripping).
    2. ``MINIMAX_MODEL`` environment variable (non-empty after
       stripping).
    3. Production default :data:`DEFAULT_MODEL`
       (``MiniMax-M3``).

    Cluster M3-1 corrective: the empty-string fallback has
    been removed. Operators who relied on the previous
    "no model = no default" behaviour MUST set
    ``MINIMAX_MODEL`` explicitly or rely on the M3 default.
    """

    def test_explicit_model_identifier_used(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        assert adapter.model_identifier == "MiniMax-M2.7"

    def test_minimax_model_env_used_when_no_argument(self) -> None:
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M2.7"

    def test_explicit_argument_overrides_env(self) -> None:
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.5"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        assert adapter.model_identifier == "MiniMax-M2.7"

    def test_no_argument_no_env_yields_minimax_m3_default(self) -> None:
        """Cluster M3-1: when neither an argument nor an env
        var is provided, the adapter defaults to
        ``MiniMax-M3``. The readiness struct still reports
        not-live when the API key is missing, but the model
        identifier on the adapter is the M3 production
        default.
        """
        os.environ.pop("MINIMAX_API_KEY", None)
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"
        r = adapter.readiness()
        assert not r.is_live()
        # The probe lands on the "API key unset" gate (the
        # first probe check); the model id is the M3 default.
        assert r.model_identifier == "MiniMax-M3"
        assert "MINIMAX_API_KEY" in (r.reason or "")

    def test_empty_string_env_treated_as_unset(self) -> None:
        """Cluster M3-1: ``MINIMAX_MODEL=""`` is treated as
        "unset" so an operator who accidentally exports an
        empty value falls back to the M3 default.
        """
        os.environ["MINIMAX_MODEL"] = ""
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"

    def test_whitespace_env_treated_as_unset(self) -> None:
        """Cluster M3-1: ``MINIMAX_MODEL="  "`` is treated as
        "unset" so an operator with stray whitespace falls
        back to the M3 default.
        """
        os.environ["MINIMAX_MODEL"] = "   "
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"

    def test_adapter_validate_against_account_with_fake_transport(self) -> None:
        """Validation is not possible with a fake transport — the
        method must report so explicitly."""
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        is_listed, available, err = adapter.validate_against_account()
        assert is_listed is False
        assert available == ()
        assert "HTTP transport" in (err or "")

    def test_adapter_validate_against_account_with_default_model(self) -> None:
        """Validation with the M3 default routes through the
        catalog lookup just like an explicit identifier.

        The test wires an HTTP transport that returns a
        catalog containing ``MiniMax-M3``; the adapter's
        default identifier is then verified as listed.
        """

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"id": "MiniMax-M3"}, {"id": "MiniMax-M2.7"}]},
            )

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        os.environ.pop("MINIMAX_MODEL", None)
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(client=client),
        )
        assert adapter.model_identifier == "MiniMax-M3"
        is_listed, available, err = adapter.validate_against_account()
        assert is_listed is True
        assert available == ("MiniMax-M3", "MiniMax-M2.7")
        assert err is None

    def test_adapter_validate_against_account_proxies_to_helper(self) -> None:
        """When the adapter uses an HTTP transport with a configured
        model id, ``validate_against_account`` proxies to the
        helper and returns its result."""

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "MiniMax-M2.7"},
                        {"id": "MiniMax-M2.5"},
                    ]
                },
            )

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        client = httpx.Client(
            transport=httpx.MockTransport(handler), timeout=DEFAULT_TIMEOUT_SECONDS
        )
        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(client=client),
            model_identifier="MiniMax-M2.7",
        )
        is_listed, available, err = adapter.validate_against_account()
        assert is_listed is True
        assert available == ("MiniMax-M2.7", "MiniMax-M2.5")
        assert err is None

    def test_readiness_with_default_model_reports_fake_transport(self) -> None:
        """Cluster M3-1: with the API key set, the default M3
        model, but a fake transport, the readiness probe
        reports ``not_live`` because the transport is not
        the production HTTP transport. The model identifier
        is the M3 default rather than ``unset``.
        """
        os.environ["MINIMAX_API_KEY"] = "sk-test"
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
        )
        r = adapter.readiness()
        assert not r.is_live()
        assert r.model_identifier == "MiniMax-M3"
        assert r.configured is True
        assert r.transport_is_live is False
        assert "transport" in (r.reason or "")

    def test_readiness_reports_fake_transport_with_key_and_model(self) -> None:
        """When the key is set, the model id is set, but the
        transport is a fake, the readiness probe returns the
        explicit ``transport is not the production HTTP transport``
        reason. The ``transport_is_live`` flag is ``False`` even
        though the adapter is fully configured."""

        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.7",
        )
        r = adapter.readiness()
        assert not r.is_live()
        assert r.configured is True
        assert r.transport_is_live is False
        assert r.transport_available is True
        assert "not the production HTTP transport" in (r.reason or "")


# ---------------------------------------------------------------------------
# Cluster M3-1: model default + protocol switch + thinking/service_tier
# ---------------------------------------------------------------------------


class TestM31ModelDefault:
    """Cluster M3-1 corrective: ``MiniMax-M3`` is the production
    default. ``MINIMAX_MODEL`` (when explicitly set to a non-empty
    string) wins; ``model_identifier`` (explicit ctor arg) wins
    over the env var. The empty-string and whitespace env cases
    fall back to the default.
    """

    def test_default_is_minimax_m3_when_env_unset(self) -> None:
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"

    def test_default_is_minimax_m3_when_env_empty_string(self) -> None:
        os.environ["MINIMAX_MODEL"] = ""
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"

    def test_default_is_minimax_m3_when_env_whitespace(self) -> None:
        os.environ["MINIMAX_MODEL"] = "   "
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M3"

    def test_env_var_overrides_default(self) -> None:
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M2.7"

    def test_explicit_arg_overrides_env(self) -> None:
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="MiniMax-M2.5",
        )
        assert adapter.model_identifier == "MiniMax-M2.5"

    def test_explicit_arg_empty_falls_back_to_env(self) -> None:
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            model_identifier="",
        )
        assert adapter.model_identifier == "MiniMax-M2.7"

    def test_does_not_silently_fallback_to_m2_7(self) -> None:
        """When neither argument nor env is set, the adapter
        does NOT silently pick ``MiniMax-M2.7`` (the previously-
        documented model). It picks the production default
        ``MiniMax-M3``.
        """
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier != "MiniMax-M2.7"
        assert adapter.model_identifier == "MiniMax-M3"


class TestM31ProtocolSwitch:
    """Cluster M3-1: native / openai-compatible protocol switch
    routes to the matching endpoint.
    """

    def test_default_protocol_is_openai_compatible(self) -> None:
        transport = HttpMiniMaxTransport()
        assert transport.protocol == "openai-compatible"
        assert transport.endpoint == DEFAULT_ENDPOINT

    def test_native_protocol_uses_native_endpoint(self) -> None:
        transport = HttpMiniMaxTransport(protocol="native")
        assert transport.protocol == "native"
        assert transport.endpoint == NATIVE_ENDPOINT

    def test_openai_compatible_protocol_uses_default_endpoint(self) -> None:
        transport = HttpMiniMaxTransport(protocol="openai-compatible")
        assert transport.protocol == "openai-compatible"
        assert transport.endpoint == DEFAULT_ENDPOINT

    def test_explicit_endpoint_overrides_protocol_default(self) -> None:
        transport = HttpMiniMaxTransport(
            protocol="native",
            endpoint="https://custom.example.com/v1/chat",
        )
        assert transport.protocol == "native"
        assert transport.endpoint == "https://custom.example.com/v1/chat"

    def test_unknown_protocol_rejected(self) -> None:
        with pytest.raises(ValueError, match="protocol"):
            HttpMiniMaxTransport(protocol="not-a-protocol")

    def test_adapter_propagates_protocol_to_transport(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            protocol="native",
        )
        # Adapter is wired to a fake transport, so the protocol
        # is carried on the adapter but the resolved transport
        # is still the fake. The adapter's protocol is what
        # gets forwarded into MiniMaxRequest.
        assert adapter._protocol == "native"  # noqa: SLF001 — internal probe

    def test_adapter_rejects_unknown_protocol(self) -> None:
        with pytest.raises(ValueError, match="protocol"):
            MiniMaxAdapter(
                transport=FakeMiniMaxTransport(),
                protocol="not-a-protocol",
            )


class TestM31ThinkingAndServiceTier:
    """Cluster M3-1: thinking + service_tier configuration
    propagates into the wire request.
    """

    def test_thinking_default_is_enabled(self) -> None:
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.thinking is True

    def test_service_tier_default_is_standard(self) -> None:
        os.environ.pop("MINIMAX_MODEL", None)
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.service_tier == "standard"

    def test_thinking_can_be_disabled(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            thinking=False,
        )
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.thinking is False

    def test_thinking_can_be_unset(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            thinking=None,
        )
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.thinking is None

    def test_service_tier_can_be_overridden(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            service_tier="priority",
        )
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.service_tier == "priority"

    def test_service_tier_can_be_unset(self) -> None:
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
            service_tier=None,
        )
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="hello",
            max_tokens=100,
            temperature=0.0,
        )
        provider_request = adapter._build_provider_request(request)  # noqa: SLF001
        assert provider_request.service_tier is None


class TestM31NativeBodySerialization:
    """Cluster M3-1: the native protocol serializes messages
    into a legacy ``prompt`` body and forwards thinking /
    service_tier verbatim.
    """

    def test_native_body_uses_prompt_field(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(MiniMaxMessage(role="user", content="hello"),),
            max_completion_tokens=100,
            temperature=0.5,
            thinking=True,
            service_tier="standard",
            protocol="native",
        )
        body = json.loads(_serialize_request_body(request, protocol="native"))
        assert body["model"] == "MiniMax-M3"
        assert body["prompt"] == "hello"
        assert body["max_tokens"] == 100
        assert body["temperature"] == 0.5
        assert body["thinking"] == {"type": "enabled"}
        assert body["service_tier"] == "standard"
        # OpenAI-shape fields are absent in the native body.
        assert "messages" not in body
        assert "max_completion_tokens" not in body

    def test_native_body_concatenates_system_and_user(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(
                MiniMaxMessage(role="system", content="be terse"),
                MiniMaxMessage(role="user", content="hi"),
            ),
            protocol="native",
        )
        body = json.loads(_serialize_request_body(request, protocol="native"))
        assert "be terse" in body["prompt"]
        assert "hi" in body["prompt"]
        # System section is marked.
        assert "[system]" in body["prompt"]

    def test_openai_compatible_body_uses_messages(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(MiniMaxMessage(role="user", content="hello"),),
            max_completion_tokens=100,
            temperature=0.5,
            thinking=True,
            service_tier="standard",
            protocol="openai-compatible",
        )
        body = json.loads(_serialize_request_body(request, protocol="openai-compatible"))
        assert body["model"] == "MiniMax-M3"
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["max_completion_tokens"] == 100
        assert body["temperature"] == 0.5
        assert body["thinking"] == {"type": "enabled"}
        assert body["service_tier"] == "standard"
        # Native-shape fields are absent in the OpenAI body.
        assert "prompt" not in body
        assert "max_tokens" not in body

    def test_openai_compatible_body_omits_unset_thinking(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(MiniMaxMessage(role="user", content="hello"),),
            thinking=None,
            service_tier=None,
            protocol="openai-compatible",
        )
        body = json.loads(_serialize_request_body(request, protocol="openai-compatible"))
        assert "thinking" not in body
        assert "service_tier" not in body


class TestM31ReadinessClassification:
    """Cluster M3-1: ProviderReadiness carries a closed-set
    ``classification`` literal. ``is_live()`` returns ``True``
    for both catalog_verified and live_verified_catalog_lag.
    """

    def test_classification_default_is_not_classified(self) -> None:
        os.environ["MINIMAX_API_KEY"] = "sk-test"
        os.environ.pop("MINIMAX_MODEL", None)
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200)),
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        adapter = MiniMaxAdapter(
            transport=HttpMiniMaxTransport(client=client),
        )
        r = adapter.readiness()
        assert r.classification == "not_classified"
        assert r.is_live() is True

    def test_classification_catalog_verified_is_live(self) -> None:
        readiness = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=True,
            model_identifier="MiniMax-M3",
            classification="live_verified_catalog",
        )
        assert readiness.is_live() is True

    def test_classification_catalog_lag_is_live(self) -> None:
        readiness = ProviderReadiness(
            configured=True,
            transport_available=True,
            transport_is_live=True,
            model_identifier="MiniMax-M3",
            classification="live_verified_catalog_lag",
        )
        assert readiness.is_live() is True

    def test_classification_not_live_is_not_live(self) -> None:
        readiness = ProviderReadiness(
            configured=False,
            transport_available=False,
            transport_is_live=False,
            model_identifier="MiniMax-M3",
            classification="not_live",
        )
        assert readiness.is_live() is False

    def test_classification_literal_is_closed(self) -> None:
        """Off-literal classification values raise at construction."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ProviderReadiness(
                configured=True,
                transport_available=True,
                transport_is_live=True,
                model_identifier="MiniMax-M3",
                classification="maybe_live",  # type: ignore[arg-type]
            )

    def test_not_live_factory_sets_classification_not_live(self) -> None:
        r = not_live(reason="test")
        assert r.classification == "not_live"


class TestM31ProductionRefusesSilentFallback:
    """Cluster M3-1: the production startup path refuses silent
    model substitution. When ``MINIMAX_MODEL`` is unset, the
    adapter reports the M3 default and the production readiness
    validator accepts the default identifier (i.e., it does
    not reject M3 as "unknown").
    """

    def test_default_model_identifier_passes_readiness(self) -> None:
        """The M3 default identifier must satisfy the readiness
        schema's ``min_length=1`` constraint so production
        startup doesn't reject it as malformed."""
        os.environ.pop("MINIMAX_MODEL", None)
        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(
            transport=FakeMiniMaxTransport(),
        )
        r = adapter.readiness()
        assert len(r.model_identifier) >= 1
        assert r.model_identifier == "MiniMax-M3"
        # The probe still flags the fake transport as not live,
        # but the model identifier is not the rejection reason.
        assert "model_identifier is empty" not in (r.reason or "")

    def test_minimax_m2_7_explicit_not_silently_overridden(self) -> None:
        """When the operator explicitly sets ``MINIMAX_MODEL`` to
        M2.7 (e.g. for testing), the adapter uses M2.7 verbatim.
        The production profile accepts this as long as the M2.7
        model is in the live catalog; the doc explicitly says
        M2.7 may remain supported for compatibility.
        """
        os.environ["MINIMAX_MODEL"] = "MiniMax-M2.7"
        os.environ["MINIMAX_API_KEY"] = "sk-test"
        adapter = MiniMaxAdapter(transport=FakeMiniMaxTransport())
        assert adapter.model_identifier == "MiniMax-M2.7"


class TestM31NativeBodyAssistantAndToolMessages:
    """Cluster M3-1: the native protocol wraps assistant /
    tool messages in ``[role]`` markers (the native endpoint
    does not distinguish them from user/system segments).
    """

    def test_native_body_wraps_assistant_in_role_marker(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(
                MiniMaxMessage(role="user", content="hi"),
                MiniMaxMessage(role="assistant", content="hello back"),
                MiniMaxMessage(role="user", content="how are you?"),
            ),
            protocol="native",
        )
        body = json.loads(_serialize_request_body(request, protocol="native"))
        assert "[assistant]" in body["prompt"]
        assert "hello back" in body["prompt"]

    def test_native_body_wraps_tool_in_role_marker(self) -> None:
        from seharness.models.minimax_transport import (
            _serialize_request_body,
        )

        request = MiniMaxRequest(
            model="MiniMax-M3",
            messages=(
                MiniMaxMessage(role="user", content="run the tool"),
                MiniMaxMessage(role="tool", content="tool output"),
            ),
            protocol="native",
        )
        body = json.loads(_serialize_request_body(request, protocol="native"))
        assert "[tool]" in body["prompt"]
        assert "tool output" in body["prompt"]


# ---------------------------------------------------------------------------
# Cluster M3-4: _parse_structured_output helper on MiniMaxAdapter
# ---------------------------------------------------------------------------


class TestParseStructuredOutput:
    """The M3-4 offline acceptance needs the MiniMax adapter to
    parse JSON ``content_text`` into ``response.parsed`` so the
    model-backed services can validate against their Pydantic
    schemas. The helper is intentionally trivial — it only
    succeeds when the entire body is a clean JSON top-level value.
    Mixed prose is not supported; the repair layer handles that.
    """

    def test_json_object_parses(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        result = _parse_structured_output('{"status": "approved", "approval": true}')
        assert result == {"status": "approved", "approval": True}

    def test_json_array_parses(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        result = _parse_structured_output("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_nested_json_parses(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        body = '{"plan": {"plan_id": "p-1", "tasks": [{"task_id": "t-1"}]}}'
        result = _parse_structured_output(body)
        assert isinstance(result, dict)
        assert "plan" in result

    def test_none_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        assert _parse_structured_output(None) is None

    def test_empty_string_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        assert _parse_structured_output("") is None
        assert _parse_structured_output("   ") is None

    def test_prose_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        # The first character is not { or [, so we skip the
        # try/except entirely and return None quickly.
        assert _parse_structured_output("Here is the plan: {...}") is None
        assert _parse_structured_output("Sure! Let me start.") is None

    def test_markdown_fenced_json_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        body = '```json\n{"x": 1}\n```'
        # Leading backticks means the first non-whitespace char is
        # `` ` ``, not ``{``, so the helper bails out.
        assert _parse_structured_output(body) is None

    def test_malformed_json_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        # Starts with { but is not valid JSON; the helper swallows
        # the JSONDecodeError and returns None.
        assert _parse_structured_output("{not valid") is None
        assert _parse_structured_output("{") is None

    def test_scalar_json_returns_none(self) -> None:
        from seharness.models.minimax import _parse_structured_output

        # Bare numbers / strings / booleans / null are valid JSON
        # but not what the structured-payload caller wants. The
        # helper only accepts objects and arrays so the caller can
        # validate them against Pydantic models.
        assert _parse_structured_output("42") is None
        assert _parse_structured_output('"hello"') is None
        assert _parse_structured_output("true") is None
        assert _parse_structured_output("null") is None


class TestParseStructuredOutputFlowsThroughInvoke:
    """End-to-end: a JSON content_text flows into ``response.parsed``
    via :meth:`MiniMaxAdapter.invoke`.
    """

    def test_invoke_sets_parsed_from_json_content(self) -> None:
        from seharness.models.minimax import MiniMaxAdapter
        from seharness.models.minimax_transport import (
            DEFAULT_MODEL,
            FakeMiniMaxTransport,
        )

        class _ScriptedTransport(FakeMiniMaxTransport):
            def complete(self, request):  # type: ignore[override]
                from seharness.models.minimax_transport import (
                    MiniMaxTransportResponse,
                )

                return MiniMaxTransportResponse(
                    content_text='{"status": "approved", "approval": true}',
                    usage_input_tokens=10,
                    usage_output_tokens=5,
                    request_id="req-1",
                    error=None,
                )

        adapter = MiniMaxAdapter(
            model_identifier=DEFAULT_MODEL,
            transport=_ScriptedTransport(),
        )
        from seharness.domain.enums import RoutingRole
        from seharness.domain.requests import ModelRequest

        response = adapter.invoke(ModelRequest(role=RoutingRole.REVIEW, prompt="hi", context={}))
        assert response.error is None
        assert response.parsed == {"status": "approved", "approval": True}
        assert response.raw_output == '{"status": "approved", "approval": true}'

    def test_invoke_parsed_none_for_prose(self) -> None:
        from seharness.models.minimax import MiniMaxAdapter
        from seharness.models.minimax_transport import (
            DEFAULT_MODEL,
            FakeMiniMaxTransport,
        )

        class _ProseTransport(FakeMiniMaxTransport):
            def complete(self, request):  # type: ignore[override]
                from seharness.models.minimax_transport import (
                    MiniMaxTransportResponse,
                )

                return MiniMaxTransportResponse(
                    content_text="Here is the plan: ...",
                    usage_input_tokens=10,
                    usage_output_tokens=5,
                    request_id="req-2",
                    error=None,
                )

        adapter = MiniMaxAdapter(
            model_identifier=DEFAULT_MODEL,
            transport=_ProseTransport(),
        )
        from seharness.domain.enums import RoutingRole
        from seharness.domain.requests import ModelRequest

        response = adapter.invoke(ModelRequest(role=RoutingRole.PLANNING, prompt="hi", context={}))
        assert response.error is None
        assert response.parsed is None
        assert response.raw_output == "Here is the plan: ..."

    def test_invoke_parsed_preserves_raw_output(self) -> None:
        """``raw_output`` is the original content_text regardless
        of whether ``parsed`` succeeded; downstream consumers that
        want the prose fallback can still read it.
        """
        from seharness.models.minimax import MiniMaxAdapter
        from seharness.models.minimax_transport import (
            DEFAULT_MODEL,
            FakeMiniMaxTransport,
        )

        class _JsonTransport(FakeMiniMaxTransport):
            def complete(self, request):  # type: ignore[override]
                from seharness.models.minimax_transport import (
                    MiniMaxTransportResponse,
                )

                return MiniMaxTransportResponse(
                    content_text='{"plan_id": "p-1"}',
                    usage_input_tokens=10,
                    usage_output_tokens=5,
                    request_id="req-3",
                    error=None,
                )

        adapter = MiniMaxAdapter(
            model_identifier=DEFAULT_MODEL,
            transport=_JsonTransport(),
        )
        from seharness.domain.enums import RoutingRole
        from seharness.domain.requests import ModelRequest

        response = adapter.invoke(ModelRequest(role=RoutingRole.PLANNING, prompt="hi", context={}))
        assert response.parsed == {"plan_id": "p-1"}
        assert response.raw_output == '{"plan_id": "p-1"}'
