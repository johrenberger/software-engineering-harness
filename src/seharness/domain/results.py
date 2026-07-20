"""Provider-neutral response models for the model-adapter layer (slice 4).

Per SPEC §10 + §6: every adapter returns the same ``ModelResponse`` shape
with the following preserved fields:

- provider            (ProviderName)
- model               (str — adapter's model identifier)
- duration            (seconds; float, always recorded even on failure)
- raw_output          (artifact path / bytes / str — adapter-defined)
- parsed              (validated structured payload or None)
- usage               (token counts when available)
- error               (normalized ModelError or None)
- requires_repair     (bool — set by adapter when structured output is malformed)
- files_changed       (tuple of relative paths the adapter wrote)

The contract forbids extra fields. Transport metadata never leaks into the
canonical response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from seharness.domain.enums import ProviderName

# Canonical, closed-set of normalized error kinds per SPEC §10.
# Adapters must map any internal failure onto one of these values.
#
# ``rate_limit`` (cluster H, story H1) is the fifth canonical kind,
# emitted when the upstream provider returns HTTP 429 or equivalent.
# The router treats ``rate_limit`` as routable: retry-with-backoff
# within the primary adapter first, then fall back to the alternate
# provider if still throttled. See ``seharness.models.router``.
ErrorKind = Literal[
    "timeout",
    "provider_failure",
    "malformed_output",
    "auth",
    "rate_limit",
]


class ModelUsage(BaseModel):
    """Token usage metadata when available."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelError(BaseModel):
    """Normalized adapter error.

    The ``kind`` field is a closed Literal so downstream routing/repair code
    can pattern-match without fear of unknown values creeping in.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    kind: ErrorKind
    message: str
    retryable: bool = False


class ModelResponse(BaseModel):
    """Provider-neutral adapter response.

    The response is the single source of truth for everything the adapter
    produced — parsed structured output, raw text, usage, errors, side
    effects (files changed). Transport metadata lives inside the adapter
    implementation; this shape is the contract that workflow code reads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    provider: ProviderName
    model: str
    parsed: dict[str, object] | None = None
    raw_output: str | None = None
    usage: ModelUsage | None = None
    error: ModelError | None = None
    requires_repair: bool = False
    files_changed: tuple[str, ...] = Field(default_factory=tuple)
    duration_s: float = Field(default=0.0, ge=0.0)


class ModelRepair(BaseModel):
    """Outcome of a single structured-output repair attempt.

    Per SPEC §10: exactly ONE repair attempt is allowed. After a single
    failed attempt the response is rejected and the router decides whether
    to fall back to another provider.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    outcome: str  # RepairOutcome value — kept as plain str so this module
    # does not have to import the enums module (avoids cycle).
    attempts: int = Field(ge=0, le=1)
    original_error: str | None = None


__all__ = [
    "ErrorKind",
    "ModelError",
    "ModelRepair",
    "ModelResponse",
    "ModelUsage",
]
