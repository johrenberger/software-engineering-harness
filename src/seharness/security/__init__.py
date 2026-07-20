"""Security primitives (cluster H).

Public surface:

- :class:`SuspiciousPayloadFilter` \u2014 rejects obvious-bad incoming
  payloads (oversized, binary, control chars, prompt-injection
  markers).
- :class:`PayloadFilterConfig` \u2014 tunable limits.
- :class:`FilterVerdict` \u2014 structured outcome.
- :data:`FilterReason` \u2014 closed Literal of trigger kinds.

Usage::

    from seharness.security import SuspiciousPayloadFilter
    verdict = SuspiciousPayloadFilter().check(some_text)
    if not verdict.ok:
        raise ValueError(f"rejected: {verdict.reasons}")
"""

from __future__ import annotations

from seharness.security.payload_filter import (
    FilterReason,
    FilterVerdict,
    PayloadFilterConfig,
    SuspiciousPayloadFilter,
)

__all__ = [
    "FilterReason",
    "FilterVerdict",
    "PayloadFilterConfig",
    "SuspiciousPayloadFilter",
]
