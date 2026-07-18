"""CI remediation packet + loop for SPEC §'Slice 10'.

Provides:
- ``RemediationReason`` StrEnum — stable enum of failure classes.
- ``RemediationPacket`` frozen dataclass — carries the bounded
  evidence needed to drive a fix.
- ``CiRemediationLoop`` Protocol + ``StubCiRemediationLoop``.

**SPEC §'20. PR and CI Flow'**: failed checks → collect failing check
metadata → collect available logs → classify failure → create CI
remediation packet. This module owns the *build_packets* step; the
actual fix loop runs in slice 10 controller wiring (deferred to slice
12 OpenClaw packaging per SPEC §'28. Build sequence').
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from seharness.validation.remediation import BoundedEvidence

from .checks import CheckConclusion, RequiredChecksView


class RemediationReason(StrEnum):
    """Closed set of CI remediation reasons."""

    CHECK_FAILED = "check_failed"
    CHECK_TIMEOUT = "check_timeout"
    CHECK_CANCELLED = "check_cancelled"
    CHECK_ACTION_REQUIRED = "check_action_required"


_CONCLUSION_TO_REASON: dict[CheckConclusion, RemediationReason] = {
    CheckConclusion.FAILURE: RemediationReason.CHECK_FAILED,
    CheckConclusion.TIMED_OUT: RemediationReason.CHECK_TIMEOUT,
    CheckConclusion.CANCELLED: RemediationReason.CHECK_CANCELLED,
    CheckConclusion.ACTION_REQUIRED: RemediationReason.CHECK_ACTION_REQUIRED,
}


@dataclass(frozen=True)
class RemediationPacket:
    """Frozen packet describing one failed required check."""

    check_name: str
    reason: RemediationReason
    bounded_evidence: BoundedEvidence


class CiRemediationLoop(Protocol):
    """Protocol for building remediation packets from a check snapshot.

    **Structural auto-merge prevention**: this Protocol has NO merge
    methods.
    """

    def build_packets(self, view: RequiredChecksView) -> tuple[RemediationPacket, ...]: ...


class StubCiRemediationLoop:
    """In-memory ``CiRemediationLoop`` for tests.

    Builds one ``RemediationPacket`` per failed required check.
    """

    def __init__(
        self,
        evidence_factory: Callable[[str], BoundedEvidence] | None = None,
    ) -> None:
        self._evidence_factory = evidence_factory

    def build_packets(self, view: RequiredChecksView) -> tuple[RemediationPacket, ...]:
        packets: list[RemediationPacket] = []
        for check in view.all_checks:
            if not check.required:
                continue
            if not check.is_failed:
                continue
            assert check.conclusion is not None
            reason = _CONCLUSION_TO_REASON.get(check.conclusion, RemediationReason.CHECK_FAILED)
            evidence = self._build_evidence(check.name)
            packets.append(
                RemediationPacket(
                    check_name=check.name,
                    reason=reason,
                    bounded_evidence=evidence,
                )
            )
        return tuple(packets)

    def _build_evidence(self, check_name: str) -> BoundedEvidence:
        if self._evidence_factory is not None:
            return self._evidence_factory(check_name)
        # Default: empty BoundedEvidence envelope
        return BoundedEvidence(
            failure=None,
            relevant_files=(),
            previous_green=None,
            allowed_paths=("src/", "tests/"),
        )
