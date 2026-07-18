"""Provider-neutral request models for the model-adapter layer (slice 4).

Per SPEC §10: the model request must be provider-neutral. Every adapter
(MiniMax, Codex, Fake, future) receives the same ``ModelRequest`` shape and
returns the same ``ModelResponse`` shape — never transport-specific data.

This module is intentionally tiny: it holds the inbound request and any
small helpers. Outcome shapes live in ``domain/results.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from seharness.domain.enums import RoutingRole


class ModelRequest(BaseModel):
    """Provider-neutral model invocation request.

    Slice 4 contract:
    - ``role`` selects the routing slot (planning, implementation, etc.).
    - ``prompt`` carries the natural-language task input.
    - ``context`` is an optional opaque dict for per-call overrides
      (temperature, max_tokens, allowed paths, etc.). Adapters that
      cannot honour a key must ignore it — never silently substitute.
    - ``max_tokens`` and ``temperature`` are the two well-known knobs.

    Adapters must NEVER receive transport-specific fields. All
    transport-shape concerns live inside the adapter implementation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    role: RoutingRole
    prompt: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


__all__ = ["ModelRequest"]
