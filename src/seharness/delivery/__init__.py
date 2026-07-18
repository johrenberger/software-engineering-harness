"""Public surface for slice 9 delivery subsystem.

Per SPEC §'19. Git Automation' + §'20. GitHub Pull Request and CI Flow' +
§'Slice 9: Git delivery'.
"""

from __future__ import annotations

from seharness.delivery.backend import GitBackend, SubprocessGitBackend
from seharness.delivery.branch import BranchFormat, BranchService
from seharness.delivery.commit import (
    AuthorizedFileSet,
    CommitMessage,
    CommitService,
    UnauthorizedFileError,
)
from seharness.delivery.gate import (
    GateFailureError,
    GateResult,
    GateRunner,
    LocalValidationGate,
)
from seharness.delivery.idempotency import (
    IdempotencyKey,
    IdempotencyRecord,
    IdempotencyStore,
)
from seharness.delivery.pr import PullRequestClient, StubPullRequestClient

__all__ = [
    "AuthorizedFileSet",
    "BranchFormat",
    "BranchService",
    "CommitMessage",
    "CommitService",
    "GateFailureError",
    "GateResult",
    "GateRunner",
    "GitBackend",
    "IdempotencyKey",
    "IdempotencyRecord",
    "IdempotencyStore",
    "LocalValidationGate",
    "PullRequestClient",
    "StubPullRequestClient",
    "SubprocessGitBackend",
    "UnauthorizedFileError",
]
