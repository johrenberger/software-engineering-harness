"""Stable requirement / scenario identifiers (slice 5).

Per SPEC §15 ("Use stable identifiers: FR-*, NFR-*, SCN-*") and §28
(slice 5 RED bullet: "requirements receive stable IDs").

The IDs are part of the harness contract:
- persisted in run-state.json and events.jsonl
- referenced by plans / traceability
- referenced by task evidence (slice 6)

Implementation: each ID is a NewType-style ``Annotated[str, ...]`` with a
Pydantic-2 ``BeforeValidator`` that rejects malformed input. The kind is
encoded by the prefix and surfaced via ``kind(raw)``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator


class RequirementKind(StrEnum):
    """Distinguishes functional / non-functional / scenario identifiers.

    The kind is encoded both in the prefix (FR / NFR / SCN) and as a
    separate value on each requirement so downstream code can branch on
    ``kind`` without re-parsing the ID string.
    """

    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    SCENARIO = "scenario"


_FR_RE = re.compile(r"^FR-([1-9][0-9]*)$")
_NFR_RE = re.compile(r"^NFR-([1-9][0-9]*)$")
_SCN_RE = re.compile(r"^SCN-([1-9][0-9]*)$")


def _check(raw: str, pattern: re.Pattern[str], prefix: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"stable ID must be a string, got {type(raw).__name__}")
    if pattern.match(raw) is None:
        raise ValueError(
            f"invalid stable ID {raw!r}: expected format '{prefix}-<positive integer>'"
        )
    return raw


def _validate_fr(raw: str) -> str:
    return _check(raw, _FR_RE, "FR")


def _validate_nfr(raw: str) -> str:
    return _check(raw, _NFR_RE, "NFR")


def _validate_scn(raw: str) -> str:
    return _check(raw, _SCN_RE, "SCN")


def _kind_for(raw: str) -> RequirementKind:
    if _FR_RE.match(raw):
        return RequirementKind.FUNCTIONAL
    if _NFR_RE.match(raw):
        return RequirementKind.NON_FUNCTIONAL
    if _SCN_RE.match(raw):
        return RequirementKind.SCENARIO
    raise ValueError(f"unknown stable ID {raw!r}")


# Branded string types — at runtime they are plain str; the BeforeValidator
# ensures only well-formed IDs ever flow through.
FunctionalRequirementId = Annotated[str, BeforeValidator(_validate_fr)]
NonFunctionalRequirementId = Annotated[str, BeforeValidator(_validate_nfr)]
ScenarioId = Annotated[str, BeforeValidator(_validate_scn)]


def requirement_kind(raw: str) -> RequirementKind:
    """Return the ``RequirementKind`` encoded in ``raw``'s prefix."""
    return _kind_for(raw)


__all__ = [
    "FunctionalRequirementId",
    "NonFunctionalRequirementId",
    "RequirementKind",
    "ScenarioId",
    "requirement_kind",
]
