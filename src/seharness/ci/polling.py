"""Polling policy + state for SPEC §'Slice 10' CI monitoring.

Provides:
- ``PollPolicy`` — frozen dataclass enforcing max attempts + max total
  time + interval_s.
- ``PollState`` — frozen dataclass tracking attempts + elapsed.
- ``PollOutcome`` — StrEnum: READY / EXHAUSTED / STILL_PENDING.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PollOutcome(StrEnum):
    """Outcome of a polling run."""

    READY = "ready"
    EXHAUSTED = "exhausted"
    STILL_PENDING = "still_pending"


@dataclass(frozen=True)
class PollPolicy:
    """Frozen dataclass enforcing a hard ceiling on polling.

    Defaults: 30s interval, 20 attempts, 1800s total — SPEC §'20. PR
    and CI Flow' practical upper bounds.
    """

    interval_s: float = 30.0
    max_attempts: int = 20
    max_total_s: float = 1800.0

    def __post_init__(self) -> None:
        if self.interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")
        if self.max_total_s <= 0:
            raise ValueError("max_total_s must be > 0")


@dataclass(frozen=True)
class PollState:
    """Frozen snapshot of polling progress."""

    attempts: int
    elapsed_s: float
    started_at: str
