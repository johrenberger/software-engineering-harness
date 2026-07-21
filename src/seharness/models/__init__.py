"""Model-adapter layer (slice 4 + cluster N MiniMax M3 refinement).

This package implements the provider-neutral model contract required by
SPEC §10. The exports below form the public API surface.

Layering:

    base.py                  — ``ModelAdapter`` abstract base class.
    fake.py                  — ``FakeModelAdapter`` (deterministic, fixture-driven).
    minimax.py               — ``MiniMaxAdapter`` (HTTP-backed; cluster N).
    minimax_transport.py     — ``MiniMaxTransport`` protocol + HTTP transport
                                + offline fakes (cluster N).
    provider_readiness.py    — ``ProviderReadiness`` capability model
                                (cluster N — replaces class-name detection).
    codex.py                 — ``CodexAdapter`` boundary (slice 4: fails closed).
    router.py                — ``ModelRouter`` (role-based dispatch + fallback).
    output_repair.py         — ``StructuredOutputRepair`` (one-shot repair on
                                malformed structured output).

Request/response shapes live in ``seharness.domain.requests`` and
``seharness.domain.results``. Enums (``ProviderName`` etc.) live in
``seharness.domain.enums``.
"""

from __future__ import annotations

from typing import Final

from seharness.domain.enums import (
    ProviderKind,
    ProviderName,
    RepairOutcome,
    RoutingRole,
)
from seharness.domain.requests import ModelRequest
from seharness.domain.results import (
    ErrorKind,
    ModelError,
    ModelRepair,
    ModelResponse,
    ModelUsage,
)
from seharness.models.base import ModelAdapter
from seharness.models.codex import CodexAdapter
from seharness.models.fake import FakeModelAdapter
from seharness.models.minimax import MiniMaxAdapter
from seharness.models.minimax_transport import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MODEL_ENV,
    DEFAULT_TIMEOUT_SECONDS,
    DEPRECATED_LEGACY_ENDPOINT,
    MODELS_ENDPOINT,
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
from seharness.models.output_repair import (
    ReattemptCallable,
    RepairResult,
    StructuredOutputRepair,
)
from seharness.models.provider_readiness import ProviderReadiness, not_live
from seharness.models.router import (
    DEFAULT_FALLBACK,
    DEFAULT_ROUTING,
    ModelRouter,
    RetryPolicy,
)

# Adapter registry — populated lazily to avoid import cycles. The slice 4
# boundaries register themselves so ``get_adapter`` can resolve a provider
# name to a class without callers having to know the concrete type.
_ADAPTER_REGISTRY: Final[dict[ProviderName, type[ModelAdapter]]] = {}


def register_adapter(cls: type[ModelAdapter]) -> type[ModelAdapter]:
    """Register a ModelAdapter subclass under its declared ``provider``.

    Idempotent: re-registering the same provider returns the existing entry
    unchanged so the slice-4 boundaries can call this at import time.
    """
    existing = _ADAPTER_REGISTRY.get(cls.provider)
    if existing is not None:
        return existing
    _ADAPTER_REGISTRY[cls.provider] = cls
    return cls


def get_adapter(provider: str | ProviderName) -> type[ModelAdapter]:
    """Resolve a provider identifier to its ModelAdapter subclass.

    Raises ``KeyError`` if the provider is not registered.
    """
    key = ProviderName(provider)
    cls = _ADAPTER_REGISTRY.get(key)
    if cls is None:
        msg = f"no adapter registered for provider: {provider!r}"
        raise KeyError(msg)
    return cls


def registered_providers() -> tuple[ProviderName, ...]:
    """Tuple of currently-registered providers (immutable view)."""
    return tuple(_ADAPTER_REGISTRY.keys())


# Register the slice 4 boundaries. The real adapters land in later slices
# and will be registered alongside these.
register_adapter(FakeModelAdapter)
register_adapter(MiniMaxAdapter)
register_adapter(CodexAdapter)


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_FALLBACK",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MODEL_ENV",
    "DEFAULT_ROUTING",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEPRECATED_LEGACY_ENDPOINT",
    "MODELS_ENDPOINT",
    "CodexAdapter",
    "ErrorKind",
    "FakeMiniMaxTransport",
    "FakeModelAdapter",
    "HttpMiniMaxTransport",
    "MiniMaxAdapter",
    "MiniMaxMessage",
    "MiniMaxRequest",
    "MiniMaxTransport",
    "MiniMaxTransportError",
    "MiniMaxTransportResponse",
    "ModelAdapter",
    "ModelError",
    "ModelRepair",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ModelUsage",
    "ProviderKind",
    "ProviderName",
    "ProviderReadiness",
    "ReattemptCallable",
    "RecordingMiniMaxTransport",
    "RepairOutcome",
    "RepairResult",
    "RetryPolicy",
    "RoutingRole",
    "StructuredOutputRepair",
    "get_adapter",
    "not_live",
    "parse_model_catalog",
    "register_adapter",
    "registered_providers",
    "validate_model_against_account",
]
