"""SPEC §'Slice 10: CI monitoring and automatic readiness' subsystem.

Public surface (re-exported):

- Checks: ``CheckStatus``, ``CheckRunState``, ``CheckConclusion``,
  ``PullRequestCheck``, ``RequiredChecksView``, ``ChecksClient``,
  ``StubChecksClient``.
- Polling: ``PollPolicy``, ``PollState``, ``PollOutcome``.
- Readiness: ``ReadinessDecision``, ``ReadyEvaluator``, ``ReadyTransition``,
  ``StubReadyTransition``.
- Remediation: ``RemediationReason``, ``RemediationPacket``,
  ``CiRemediationLoop``, ``StubCiRemediationLoop``.
- Monitor: ``PollResult``, ``CiMonitor``, ``StubCiMonitor``.

**SPEC §'Do not merge automatically.'** enforced via:
1. Structural: ``ChecksClient``, ``ReadyTransition``, ``CiRemediationLoop``,
   ``CiMonitor`` Protocols declare NO ``merge*`` / ``auto_merge*``
   methods.
2. Runtime: ``tests/unit/ci/test_no_auto_merge.py`` parametrize-checks
   every Protocol + every Stub class for forbidden method names.
3. Source-level: no file in ``seharness.ci.*`` contains
   ``gh pr merge`` or ``merge_pull_request`` (verified by
   ``test_no_auto_merge::test_ci_module_source_does_not_call_gh_pr_merge``).
"""

from .checks import (
    CheckConclusion,
    CheckRunState,
    ChecksClient,
    CheckStatus,
    PullRequestCheck,
    RequiredChecksView,
    StubChecksClient,
)
from .monitor import CiMonitor, PollResult, StubCiMonitor
from .polling import PollOutcome, PollPolicy, PollState
from .readiness import (
    ReadinessDecision,
    ReadyEvaluator,
    ReadyTransition,
    StubReadyTransition,
)
from .remediation import (
    CiRemediationLoop,
    RemediationPacket,
    RemediationReason,
    StubCiRemediationLoop,
)

__all__ = [
    "CheckConclusion",
    "CheckRunState",
    "CheckStatus",
    "ChecksClient",
    "CiMonitor",
    "CiRemediationLoop",
    "PollOutcome",
    "PollPolicy",
    "PollResult",
    "PollState",
    "PullRequestCheck",
    "ReadinessDecision",
    "ReadyEvaluator",
    "ReadyTransition",
    "RemediationPacket",
    "RemediationReason",
    "RequiredChecksView",
    "StubChecksClient",
    "StubCiMonitor",
    "StubCiRemediationLoop",
    "StubReadyTransition",
]
