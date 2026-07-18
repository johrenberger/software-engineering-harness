"""Reviewer Protocol + StaticReviewer test implementation.

Per SPEC §'Slice 8 RED bullet 1':
'Reviewer receives fresh context':
- The Reviewer invocation MUST NOT receive prior implementation chat
  history or execution trace events.
- It MUST receive: approved_spec, impact, plan, final_diff,
  validation_results, coverage_results.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from seharness.review.finding import Finding


class ReviewContext(Protocol):
    """Shape of the context passed to Reviewer.review().

    Implementations MUST expose only:
    - approved_spec
    - impact
    - plan
    - final_diff
    - validation_results
    - coverage_results
    """

    approved_spec: object
    impact: object
    plan: object
    final_diff: str
    validation_results: object
    coverage_results: Mapping[str, object]


class Reviewer(Protocol):
    """Reviewer protocol. Each .review() call is a fresh invocation."""

    def review(self, ctx: object) -> Iterable[Finding]: ...


class StaticReviewer:
    """Deterministic reviewer used in tests.

    Returns the configured findings list. The list is returned as-is
    (read-only view) so callers cannot mutate it.
    """

    def __init__(self, findings: tuple[Finding, ...] = ()) -> None:
        self._findings: tuple[Finding, ...] = findings

    def review(self, ctx: object) -> tuple[Finding, ...]:
        return self._findings


class LlmReviewer:
    """Stub for a future LLM-backed reviewer (slice 10 wires real adapter).

    Constructing one raises NotImplementedError — the contract is defined
    here but not implemented. Slice 10 (CI monitoring) wires the model
    adapter.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(
            "LlmReviewer lands in slice 10 (CI monitoring) where model adapters are wired up."
        )
