"""Structured-output repair for the model-adapter layer (slice 4).

Per SPEC §10:

    Malformed structured output triggers ONE repair attempt.
    After the single repair attempt, the response is either accepted or
    rejected. The router decides whether to fall back on a rejection.

This module keeps transport-free repair logic. It does NOT know how to
parse JSON or how to talk to any specific provider. It takes a callable
``reattempt`` that the caller supplies (usually the same adapter, or a
specialised repair adapter that re-asks the model with stricter
instructions).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from seharness.domain.enums import RepairOutcome, RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelResponse

ReattemptCallable = Callable[[ModelRequest], ModelResponse]


@dataclass(frozen=True)
class RepairResult:
    """Outcome of a single structured-output repair attempt."""

    outcome: RepairOutcome
    response: ModelResponse
    attempts: int


class StructuredOutputRepair:
    """One-shot structured-output repair.

    Behaviour:

    - If the response is well-formed (``error is None`` or
      ``requires_repair is False``): return ``NOT_NEEDED`` with the
      original response.
    - If the response is malformed and no ``reattempt`` callable was
      supplied: return ``REJECTED`` with the original response (the
      caller is responsible for the next decision).
    - If the response is malformed and ``reattempt`` was supplied: call
      it EXACTLY ONCE. On success return ``REPAIRED`` with the new
      response. On failure return ``REJECTED`` with the new response.
    """

    def maybe_repair(
        self,
        response: ModelResponse,
        *,
        reattempt: ReattemptCallable | None = None,
        request: ModelRequest | None = None,
    ) -> RepairResult:
        # Case 1: no repair needed.
        # Two distinct no-repair-needed situations:
        #  (a) well-formed response: no error at all
        #  (b) terminal failure flagged by the adapter (requires_repair=False):
        #      the adapter already gave up; repair must not be attempted.
        # Only ``requires_repair=True with an error`` indicates the response
        # is a candidate for a single repair attempt.
        if response.error is None:
            return RepairResult(
                outcome=RepairOutcome.NOT_NEEDED,
                response=response,
                attempts=0,
            )
        if not response.requires_repair:
            return RepairResult(
                outcome=RepairOutcome.REJECTED,
                response=response,
                attempts=0,
            )

        # Case 2: repair needed but no callable supplied.
        if reattempt is None:
            return RepairResult(
                outcome=RepairOutcome.REJECTED,
                response=response,
                attempts=0,
            )

        # Case 3: one and only one repair attempt.
        new_response = (
            reattempt(request)
            if request is not None
            else reattempt(
                ModelRequest(
                    role=RoutingRole.PLANNING,
                    prompt="<repair-attempt>",
                )
            )
        )
        if new_response.error is None and not new_response.requires_repair:
            return RepairResult(
                outcome=RepairOutcome.REPAIRED,
                response=new_response,
                attempts=1,
            )
        return RepairResult(
            outcome=RepairOutcome.REJECTED,
            response=new_response,
            attempts=1,
        )


__all__ = ["ReattemptCallable", "RepairResult", "StructuredOutputRepair"]
