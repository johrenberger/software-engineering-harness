"""Domain enums for the workflow state machine.

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
