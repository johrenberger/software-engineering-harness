"""Provider-neutral ModelAdapter base class.

Per SPEC §10:

    class ModelAdapter(ABC):
        @abstractmethod
        def invoke(self, request: ModelRequest) -> ModelResponse: ...

Every concrete adapter — MiniMax, Codex, Fake, future — inherits from
``ModelAdapter`` and implements ``invoke``. The contract is intentionally
narrow: one method, one request type, one response type. There are no
hooks, no transport-specific overrides, no factory methods on the base.

Side-effect methods (e.g. ``write_source_change`` on the fake adapter) live
on concrete subclasses — never on the base — so the contract stays clean
and mutation testing can target each adapter's branching in isolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from seharness.domain.enums import ProviderKind, ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelResponse


class ModelAdapter(ABC):
    """Abstract base for all model providers.

    Subclasses MUST declare two class-level attributes so the router can
    discover them without instantiating the adapter:

    - ``provider: ProviderName`` — the canonical provider identifier.
    - ``kind: ProviderKind`` — adapter implementation kind (live/local/fake).

    Subclasses MUST implement ``invoke``.
    """

    #: Canonical provider identifier.
    provider: ProviderName

    #: Implementation kind (live HTTP, local subprocess, fake fixture loader).
    kind: ProviderKind

    @abstractmethod
    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Run the model and return a provider-neutral response.

        Implementations MUST NOT raise on adapter-level failures
        (timeouts, HTTP errors, malformed output). Instead they MUST
        return a ``ModelResponse`` with ``error`` populated so workflow
        code can route on a single uniform shape.
        """
        raise NotImplementedError


__all__ = ["ModelAdapter"]
