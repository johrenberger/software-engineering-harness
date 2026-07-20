"""RED tests for Cluster H, story H1: rate-limit retry-with-fallback.

Covers:

- :class:`RetryPolicy` validation (max_attempts, initial_backoff_s,
  max_backoff_s) and backoff math.
- :class:`ModelRouter` rate-limit retry path: bounded retries with
  exponential backoff, then fallback if still rate-limited.
- Backwards compatibility: existing failure kinds (provider_failure,
  timeout, malformed_output) are not affected by the new path.
"""

from __future__ import annotations

import pytest

from seharness.domain.enums import (
    ProviderKind,
    ProviderName,
    RoutingRole,
)
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelError, ModelResponse
from seharness.models import ModelRouter, RetryPolicy

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _SequenceAdapter:
    """Adapter that returns a scripted sequence of responses.

    Each ``invoke`` pops the next response off the queue. The
    response can be a successful ``ModelResponse`` (no error) or a
    ``ModelError`` (with ``kind``) wrapped in a ``ModelResponse``.

    Records every call so tests can assert how many retries happened.
    """

    def __init__(
        self,
        provider: ProviderName,
        responses: list[ModelResponse],
    ) -> None:
        self.provider = provider
        self.kind = ProviderKind.FAKE
        self._responses = list(responses)
        self.calls: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not self._responses:
            # No more scripted responses — default to a hard failure so
            # tests don't silently miss exhausted-script bugs.
            return ModelResponse(
                provider=self.provider,
                model=f"{self.provider}-test",
                error=ModelError(
                    kind="provider_failure",
                    message="scripted responses exhausted",
                ),
            )
        return self._responses.pop(0)

    @property
    def remaining(self) -> int:
        return len(self._responses)


def _ok(provider: ProviderName) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=f"{provider}-test",
        parsed={"ok": True},
    )


def _rate_limited(provider: ProviderName, message: str = "429") -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=f"{provider}-test",
        error=ModelError(kind="rate_limit", message=message, retryable=True),
    )


def _provider_failure(provider: ProviderName, message: str = "boom") -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=f"{provider}-test",
        error=ModelError(kind="provider_failure", message=message),
    )


# ---------------------------------------------------------------------------
# RetryPolicy unit tests
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_default_policy_has_three_attempts(self) -> None:
        p = RetryPolicy()
        assert p.max_attempts == 3

    def test_validation_max_attempts(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryPolicy(max_attempts=0)

    def test_validation_initial_backoff_negative(self) -> None:
        with pytest.raises(ValueError, match="initial_backoff_s"):
            RetryPolicy(initial_backoff_s=-1.0)

    def test_validation_max_below_initial(self) -> None:
        with pytest.raises(ValueError, match="max_backoff_s"):
            RetryPolicy(initial_backoff_s=10.0, max_backoff_s=1.0)

    def test_backoff_doubles(self) -> None:
        p = RetryPolicy(initial_backoff_s=1.0, max_backoff_s=100.0)
        assert p.backoff_seconds(0) == 1.0
        assert p.backoff_seconds(1) == 2.0
        assert p.backoff_seconds(2) == 4.0
        assert p.backoff_seconds(3) == 8.0

    def test_backoff_capped_at_max(self) -> None:
        p = RetryPolicy(initial_backoff_s=1.0, max_backoff_s=5.0)
        # 1, 2, 4, 8 -> capped at 5
        assert p.backoff_seconds(3) == 5.0
        assert p.backoff_seconds(10) == 5.0

    def test_sleep_uses_injected_sleeper(self) -> None:
        sleeps: list[float] = []
        p = RetryPolicy(
            max_attempts=3,
            initial_backoff_s=0.5,
            max_backoff_s=10.0,
            sleeper=sleeps.append,
        )
        p.sleep(0)
        p.sleep(1)
        p.sleep(2)
        assert sleeps == [0.5, 1.0, 2.0]

    def test_backoff_negative_attempt_raises(self) -> None:
        p = RetryPolicy()
        with pytest.raises(ValueError, match="attempt_index"):
            p.backoff_seconds(-1)


# ---------------------------------------------------------------------------
# ModelRouter rate-limit retry path
# ---------------------------------------------------------------------------


class TestModelRouterRateLimitRetry:
    def test_first_attempt_success_skips_retry(self) -> None:
        """If the primary succeeds on the first call, no retries happen."""
        primary = _SequenceAdapter(ProviderName.MINIMAX, [_ok(ProviderName.MINIMAX)])
        fallback = _SequenceAdapter(ProviderName.CODEX, [])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        assert response.error is None
        assert primary.remaining == 0
        assert len(fallback.calls) == 0

    def test_rate_limit_retries_then_succeeds(self) -> None:
        """Two rate-limit errors, then success on the third try."""
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [
                _rate_limited(ProviderName.MINIMAX, "429"),
                _rate_limited(ProviderName.MINIMAX, "429"),
                _ok(ProviderName.MINIMAX),
            ],
        )
        fallback = _SequenceAdapter(ProviderName.CODEX, [])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(max_attempts=3, sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        assert response.error is None
        assert len(primary.calls) == 3
        assert len(fallback.calls) == 0  # never fell back

    def test_rate_limit_exhausted_falls_back(self) -> None:
        """If retries are exhausted, the router falls back to the alternate provider."""
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [
                _rate_limited(ProviderName.MINIMAX),
                _rate_limited(ProviderName.MINIMAX),
                _rate_limited(ProviderName.MINIMAX),
            ],
        )
        fallback = _SequenceAdapter(ProviderName.CODEX, [_ok(ProviderName.CODEX)])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(max_attempts=3, sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        assert response.error is None
        assert response.provider == ProviderName.CODEX
        # 3 primary attempts, then 1 fallback attempt = 4 total.
        assert len(primary.calls) == 3
        assert len(fallback.calls) == 1

    def test_rate_limit_max_attempts_one_means_no_retry(self) -> None:
        """With ``max_attempts=1`` the router does NOT retry at all."""
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [_rate_limited(ProviderName.MINIMAX), _ok(ProviderName.MINIMAX)],
        )
        fallback = _SequenceAdapter(ProviderName.CODEX, [_ok(ProviderName.CODEX)])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(max_attempts=1, sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        assert response.provider == ProviderName.CODEX
        assert len(primary.calls) == 1  # no retries
        assert len(fallback.calls) == 1

    def test_rate_limit_retry_then_still_rate_limited_returns_error(self) -> None:
        """If even the fallback is rate-limited, return the error (no infinite loop)."""
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [_rate_limited(ProviderName.MINIMAX), _rate_limited(ProviderName.MINIMAX)],
        )
        fallback = _SequenceAdapter(
            ProviderName.CODEX, [_rate_limited(ProviderName.CODEX)]
        )
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(max_attempts=2, sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        assert response.error is not None
        assert response.error.kind == "rate_limit"
        assert response.provider == ProviderName.CODEX  # fallback was tried once

    def test_non_rate_limit_failure_does_not_retry(self) -> None:
        """A non-rate-limit failure on the first call skips retries entirely."""
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [_provider_failure(ProviderName.MINIMAX), _ok(ProviderName.MINIMAX)],
        )
        fallback = _SequenceAdapter(ProviderName.CODEX, [])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(max_attempts=3, sleeper=lambda _s: None),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        response = router.invoke(request)
        # provider_failure triggers fallback (existing behavior).
        assert response.provider == ProviderName.CODEX
        # But the primary is only invoked once — no retries.
        assert len(primary.calls) == 1

    def test_sleeper_is_called_with_correct_backoff(self) -> None:
        """The sleeper is invoked once per retry with the computed backoff."""
        sleeps: list[float] = []
        primary = _SequenceAdapter(
            ProviderName.MINIMAX,
            [
                _rate_limited(ProviderName.MINIMAX),
                _rate_limited(ProviderName.MINIMAX),
                _ok(ProviderName.MINIMAX),
            ],
        )
        fallback = _SequenceAdapter(ProviderName.CODEX, [])
        router = ModelRouter(
            adapters={ProviderName.MINIMAX: primary, ProviderName.CODEX: fallback},
            routing={RoutingRole.PLANNING: ProviderName.MINIMAX},
            fallback_table={ProviderName.MINIMAX: ProviderName.CODEX},
            retry_policy=RetryPolicy(
                max_attempts=3,
                initial_backoff_s=2.0,
                max_backoff_s=100.0,
                sleeper=sleeps.append,
            ),
        )
        request = ModelRequest(role=RoutingRole.PLANNING, prompt="hello")
        router.invoke(request)
        # Two retries, each with the appropriate backoff.
        assert sleeps == [2.0, 4.0]


# ---------------------------------------------------------------------------
# ErrorKind enum (domain layer)
# ---------------------------------------------------------------------------


class TestErrorKindLiteral:
    def test_rate_limit_is_a_valid_error_kind(self) -> None:
        """The Literal type accepts ``rate_limit``."""
        err = ModelError(kind="rate_limit", message="429", retryable=True)
        assert err.kind == "rate_limit"

    def test_existing_kinds_still_valid(self) -> None:
        """Backwards compatibility: existing error kinds still parse."""
        for kind in ("timeout", "provider_failure", "malformed_output", "auth"):
            err = ModelError(kind=kind, message="x")  # type: ignore[arg-type]
            assert err.kind == kind
