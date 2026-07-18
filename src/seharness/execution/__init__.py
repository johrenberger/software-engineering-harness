"""Slice 6 \u2014 TDD-aware task execution.

Public surface re-exported here so callers can write
``from seharness.execution import TaskExecutionService`` rather than
reaching into submodules.
"""

from __future__ import annotations

from seharness.execution.completion import (
    CompletionRejection,
    TaskCompletionValidator,
)
from seharness.execution.evidence import (
    FailureKind,
    GreenResult,
    RedResult,
    TaskEvidenceLayout,
)
from seharness.execution.paths import (
    AllowedPaths,
    PathAuthorizationRule,
    ProhibitedPaths,
)
from seharness.execution.service import (
    Runner,
    TaskEvidenceError,
    TaskExecutionService,
    TaskNotFoundError,
    TaskResult,
)
from seharness.execution.workspace import (
    PathClassifier,
    PreRedViolation,
    WorkspaceSnapshot,
    detect_pre_red_violations,
    revert_unauthorized,
)

__all__ = [
    "AllowedPaths",
    "CompletionRejection",
    "FailureKind",
    "GreenResult",
    "PathAuthorizationRule",
    "PathClassifier",
    "PreRedViolation",
    "ProhibitedPaths",
    "RedResult",
    "Runner",
    "TaskCompletionValidator",
    "TaskEvidenceError",
    "TaskEvidenceLayout",
    "TaskExecutionService",
    "TaskNotFoundError",
    "TaskResult",
    "WorkspaceSnapshot",
    "detect_pre_red_violations",
    "revert_unauthorized",
]
