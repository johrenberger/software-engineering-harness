"""Domain enums for the workflow state machine and slice 4 model layer.

These names are part of the harness contract:
- They are persisted in run-state.json and events.jsonl.
- They appear in CLI flags and Telegram commands.
- They map 1:1 to the canonical phase names in the harness instructions.

Renaming any value here is a breaking change for downstream slices
and for any on-disk run directories from previous versions.
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Top-level run lifecycle state."""

    CREATED = "created"
    RUNNING = "running"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


class PhaseName(StrEnum):
    """Workflow phases and terminal markers.

    Phases INTAKE through CI_MONITORING are the working pipeline; COMPLETED,
    BLOCKED, and FAILED are terminal markers used both as run status and as
    routing targets for transitions.
    """

    INTAKE = "intake"
    DISCOVERY = "discovery"
    SPECIFICATION = "specification"
    IMPACT = "impact"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    REMEDIATION = "remediation"
    REVIEW = "review"
    DELIVERY = "delivery"
    CI_MONITORING = "ci_monitoring"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Slice 4 — model layer enums
# ---------------------------------------------------------------------------


class ProviderName(StrEnum):
    """Provider identifier shared by configuration, routing, and adapter registry.

    Slice 1 defined ``ProviderName`` as a Literal in ``config.py``. Slice 4
    promotes it to a StrEnum so the same type can be used inside Pydantic
    models and the adapter registry. ``config.py`` re-exports this enum so
    existing imports keep working unchanged.
    """

    MINIMAX = "minimax"
    CODEX = "codex"


class ProviderKind(StrEnum):
    """Adapter implementation kind.

    - ``live`` — adapter makes a real network call (e.g. MiniMax HTTP).
    - ``local`` — adapter shells out to a local runtime (e.g. Codex).
    - ``fake`` — adapter returns scripted fixtures for deterministic tests.
    """

    LIVE = "live"
    LOCAL = "local"
    FAKE = "fake"


class RoutingRole(StrEnum):
    """Workflow role for routing decisions (per SPEC §10 default routing).

    The default routing table is:
        Planning: MiniMax
        Implementation: Codex
        Remediation: Codex
        Review: MiniMax
        Delivery: MiniMax
    """

    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    REMEDIATION = "remediation"
    REVIEW = "review"
    DELIVERY = "delivery"


class RepairOutcome(StrEnum):
    """Outcome of a single structured-output repair attempt.

    Per SPEC §10: exactly ONE repair attempt is allowed. After a single
    failed attempt the response is rejected and routed to the model router
    for fallback decisions.
    """

    NOT_NEEDED = "not_needed"
    REPAIRED = "repaired"
    REJECTED = "rejected"
