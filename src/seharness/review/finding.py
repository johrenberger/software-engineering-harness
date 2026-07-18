"""Finding model + severity/category/status enums.

Per SPEC §'6. Review result' and §'18. Independent Review'.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(StrEnum):
    """Closed enum. Per SPEC §'18. Independent Review':

    Blocking: critical, high, policy_blocking_medium.
    Non-blocking: medium, low, info.
    """

    CRITICAL = "critical"
    HIGH = "high"
    POLICY_BLOCKING_MEDIUM = "policy_blocking_medium"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(StrEnum):
    """Closed enum. Domain buckets for finding classification."""

    CORRECTNESS = "correctness"
    SECURITY = "security"
    POLICY = "policy"
    COVERAGE = "coverage"
    PERFORMANCE = "performance"
    STYLE = "style"


class FindingStatus(StrEnum):
    """Closed enum. Lifecycle status of a finding."""

    OPEN = "open"
    FIXED = "fixed"
    WONT_FIX = "wont_fix"


class Finding(BaseModel):
    """A review finding. Per SPEC §'6. Review result'.

    Required: id, severity, category, file, line, evidence, consequence,
    required_action.

    Optional: status (default OPEN), impacted_gates, impacted_files.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    severity: FindingSeverity
    category: FindingCategory
    file: str = Field(min_length=1)
    line: int = Field(ge=0)
    evidence: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    required_action: str = Field(min_length=1)
    status: FindingStatus = FindingStatus.OPEN
    impacted_gates: tuple[str, ...] = ()
    impacted_files: tuple[str, ...] = ()
