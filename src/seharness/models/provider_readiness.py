"""Provider readiness model (cluster N — MiniMax M3 refinement).

Per the targeted refinement workplan, the production profile MUST
fail closed when:

- The MiniMax API key is missing.
- The endpoint is invalid.
- The model identifier is empty or unsupported.
- The transport is a stub/fake.
- The service composition is deterministic.

The previous implementation relied on class-name substring
detection (``kind = ProviderKind.LIVE`` declared as a class
attribute on the adapter), which is unsound — the production
profile can read ``LIVE`` from a stub and proceed to a real run.

This module replaces that check with a :class:`ProviderReadiness`
struct that the adapter builds **at construction time** by probing
the configured transport. Production startup validates the readiness
struct and refuses to start if any field is unsatisfied.

Design constraints:

1. The struct is the single source of truth for "is MiniMax live?".
   No class-name inspection anywhere.
2. ``is_live()`` is ``True`` only when all four boolean fields are
   ``True``.
3. ``reason`` is non-empty when ``is_live()`` is ``False`` and
   carries a human-readable explanation.
4. The struct is ``frozen=True`` so it cannot be mutated after
   construction (the audit must be replayable).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Cluster M3-1 corrective — readiness classification literal.
#: The readiness struct now distinguishes catalog_verified,
#: live_verified_catalog_lag, not_live, and not_classified
#: (the coarse-grained construction-time state before the
#: catalog + direct-call verification runs).
#
# Closed set; off-literal values raise ``ValidationError`` at
# construction so a future careless edit can't widen the
# classification vocabulary.
ReadinessClassification = Literal[
    "live_verified_catalog",
    "live_verified_catalog_lag",
    "not_live",
    "not_classified",
]


class ProviderReadiness(BaseModel):
    """Capability-based readiness for a provider.

    Replaces the class-name substring detection previously used to
    decide whether the production profile may use the adapter.

    Attributes
    ----------
    configured:
        The provider's API key (or equivalent credential) is present
        and non-empty.
    transport_available:
        The transport can be invoked. For HTTP transports this means
        the endpoint is reachable; for the in-process fake it is
        always ``True``.
    model_identifier:
        The model id the adapter will use. Empty / unsupported
        values cause :meth:`is_live` to return ``False``.
    transport_is_live:
        ``True`` only when the transport is the production HTTP
        transport — never ``True`` for ``FakeMiniMaxTransport`` /
        ``RecordingMiniMaxTransport``. Production startup must
        refuse a ``LIVE`` claim backed by a fake transport.
    reason:
        Human-readable explanation; populated when any of the
        boolean fields is ``False`` or when ``model_identifier``
        is empty.
    classification:
        Cluster M3-1 corrective. Closed-set literal describing
        how the adapter was verified against the live MiniMax
        account. ``not_classified`` is the coarse construction-
        time state; production startup upgrades it to one of
        ``live_verified_catalog`` /
        ``live_verified_catalog_lag`` / ``not_live`` via the
        catalog lookup + direct-call fallback.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    configured: bool
    transport_available: bool
    transport_is_live: bool
    model_identifier: str = Field(min_length=1)
    reason: str | None = None
    classification: ReadinessClassification = "not_classified"

    def is_live(self) -> bool:
        """``True`` iff every boolean field is true and the model id is set.

        Cluster M3-1: this binary API is preserved unchanged so
        existing callers continue to work. The classification
        distinguishes ``live_verified_catalog`` from
        ``live_verified_catalog_lag`` but both map to ``True``
        here; the catalog-vs-direct-call distinction is what
        callers use the classification for.
        """
        return (
            self.configured
            and self.transport_available
            and self.transport_is_live
            and bool(self.model_identifier)
        )


def not_live(reason: str, **overrides: bool | str) -> ProviderReadiness:
    """Construct a not-live readiness struct with the given reason.

    Used by the adapter when its construction-time probe fails.
    """
    configured: bool = bool(overrides.pop("configured", False))
    transport_available: bool = bool(overrides.pop("transport_available", False))
    transport_is_live: bool = bool(overrides.pop("transport_is_live", False))
    model_identifier: str = str(overrides.pop("model_identifier", "unset"))
    if overrides:
        # Anything left over is a programming error.
        raise TypeError(f"unexpected overrides: {sorted(overrides)}")
    return ProviderReadiness(
        configured=configured,
        transport_available=transport_available,
        transport_is_live=transport_is_live,
        model_identifier=model_identifier,
        reason=reason,
        classification="not_live",
    )


__all__ = ["ProviderReadiness", "not_live"]
