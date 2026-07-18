"""Codex adapter boundary (slice 4).

Slice 4 ships the contract and the no-network boundary. Per user
decision (B): the real adapter implementation lands in a later slice.

Per SPEC §10: the Codex adapter 'must not own Git delivery'. The
boundary class therefore has no branch / push / PR methods and the
implementation will not be allowed to grow them.
"""

from __future__ import annotations

from seharness.domain.enums import ProviderKind, ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelError, ModelResponse
from seharness.models.base import ModelAdapter


class CodexAdapter(ModelAdapter):
    """Boundary for the Codex provider (local subprocess runtime)."""

    provider: ProviderName = ProviderName.CODEX
    kind: ProviderKind = ProviderKind.LOCAL

    def __init__(
        self,
        *,
        working_dir: str | None = None,
        model_identifier: str = "codex/local-stub",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._working_dir = working_dir
        self._model_identifier = model_identifier
        self._timeout_seconds = float(timeout_seconds)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        # Slice 4 boundary: fail closed with a normalized provider_failure.
        # Real subprocess wiring lands in a later slice.
        msg = (
            "CodexAdapter subprocess transport is not yet implemented in slice 4; "
            f"working_dir={self._working_dir!r}"
        )
        return ModelResponse(
            provider=self.provider,
            model=self._model_identifier,
            parsed=None,
            error=ModelError(kind="provider_failure", message=msg),
            requires_repair=False,
        )


__all__ = ["CodexAdapter"]
