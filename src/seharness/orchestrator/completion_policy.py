"""Cluster M3-3 corrective: local completion policy.

The corrective doc §"Canonical orchestrator integration" /
"Local completion" requires remote PR and CI phases to be
explicitly marked when the run is in local-acceptance mode:

> "Remote PR and CI phases must be explicitly marked:
>
> ```text
> skipped_by_local_m3_acceptance_policy
> ```
>
> Do not create synthetic URLs or assume CI readiness."

This module owns the policy object the orchestrator reads at
phase boundaries. It is a small, standalone dataclass so the
M3-3 PR can ship without dragging in the rest of the
orchestrator's wiring.
"""

from __future__ import annotations

from dataclasses import dataclass

from seharness.orchestrator.types import PhaseName

#: The literal skip-reason the corrective doc requires when
#: the local-acceptance policy is active. Exposed as a module
#: constant so tests, the orchestrator, and the run ledger can
#: reference the same string without copy-paste drift.
SKIP_REASON_LOCAL_M3_ACCEPTANCE: str = "skipped_by_local_m3_acceptance_policy"


#: Phases the local-acceptance policy short-circuits. Both
#: phases touch external systems (GitHub / CI) which are
#: explicitly out of scope for the M3 vertical workflow
#: (the doc says "The workflow stops before remote GitHub
#: push, pull-request creation, or external CI.").
LOCAL_ACCEPTANCE_SKIPPED_PHASES: tuple[PhaseName, ...] = (
    PhaseName.DRAFT_PR,
    PhaseName.CI,
)


@dataclass(frozen=True)
class LocalCompletionPolicy:
    """Per-run completion policy.

    ``remote_phases_skip_reason`` is the literal string the doc
    requires for DRAFT_PR / CI under local acceptance. An empty
    string (the default) means "actually run remote phases"
    (the cluster-N default for PR-creating workflows).

    The dataclass is frozen so the policy cannot be silently
    mutated between phase invocations. Operators set it once
    on the orchestrator constructor and the orchestrator reads
    it from a single attribute.
    """

    remote_phases_skip_reason: str = ""

    @property
    def is_local_only(self) -> bool:
        """True when the policy short-circuits remote phases."""
        return bool(self.remote_phases_skip_reason)

    def should_skip(self, phase: PhaseName) -> bool:
        """Return True when ``phase`` is short-circuited by the policy.

        Only phases in :data:`LOCAL_ACCEPTANCE_SKIPPED_PHASES`
        are candidates for skipping. Other phases always run.
        """
        if not self.is_local_only:
            return False
        return phase in LOCAL_ACCEPTANCE_SKIPPED_PHASES


__all__ = [
    "LOCAL_ACCEPTANCE_SKIPPED_PHASES",
    "SKIP_REASON_LOCAL_M3_ACCEPTANCE",
    "LocalCompletionPolicy",
]
