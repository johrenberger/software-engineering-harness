"""Slice 7 \u2014 Validation runner, classifier, remediation, retry budgets.

Public surface re-exported here so callers can write
``from seharness.validation import RemediationController`` rather than
reaching into submodules.
"""

from __future__ import annotations

from seharness.validation.classifier import (
    ClassificationError,
    FailureClassifier,
)
from seharness.validation.remediation import (
    BoundedEvidence,
    BoundedEvidenceBuilder,
    BoundedEvidenceBuildError,
    RegressionTestNotFailing,
    RegressionTestRequired,
    RelevantFile,
    RemediationController,
    RemediationResult,
    RunnerFunc,
    WeakeningDetected,
)
from seharness.validation.retry import (
    RetriesExhausted,
    RetryBudget,
    RetryBudgetRegistry,
)
from seharness.validation.runner import (
    CommandResult,
    FailureKind,
    NormalizedFailure,
    SubprocessRunner,
    ValidationRunner,
)
from seharness.validation.weakening import (
    TestWeakeningDetector,
    Weakening,
    WeakeningKind,
)

__all__ = [
    "BoundedEvidence",
    "BoundedEvidenceBuildError",
    "BoundedEvidenceBuilder",
    "ClassificationError",
    "CommandResult",
    "FailureClassifier",
    "FailureKind",
    "NormalizedFailure",
    "RegressionTestNotFailing",
    "RegressionTestRequired",
    "RelevantFile",
    "RemediationController",
    "RemediationResult",
    "RetriesExhausted",
    "RetryBudget",
    "RetryBudgetRegistry",
    "RunnerFunc",
    "SubprocessRunner",
    "TestWeakeningDetector",
    "ValidationRunner",
    "Weakening",
    "WeakeningDetected",
    "WeakeningKind",
]
