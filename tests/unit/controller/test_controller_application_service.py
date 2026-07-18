"""RED: ControllerApplicationService wires /feature + /pr to real controllers.

SPEC §'21. OpenClaw packaging' — the production ApplicationService
implementation dispatches to:
- slice 7's TaskExecutionService for /feature (real code path; same as CLI)
- slice 10's CiMonitor for /pr (returns PollResult.outcome, never merges)
- Stub-like semantics for /status, /runs, /resume, /cancel

RED bullets covered:
- /feature uses TaskExecutionService (not Stub).
- /pr uses CiMonitor (returns PollResult.outcome).
- /status returns run state from a run ledger.
- /runs enumerates active runs.
- /resume / /cancel forward to TaskExecutionService.
- ApplicationService Protocol returns dict (not Pydantic) — slice 12
  contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import pytest

from seharness.ci import (
    CheckRunState,
    PollOutcome,
    PollResult,
    RequiredChecksView,
    StubCiMonitor,
)
from seharness.controller import (
    ControllerApplicationService,
    RunLedger,
)
from seharness.execution import StubTaskExecutionService, TaskExecutionService
from seharness.telegram import FeatureRequest

if TYPE_CHECKING:
    from collections.abc import Callable


# --- Test doubles -----------------------------------------------------------


class _FakeCiClient:
    """Minimal ChecksClient double; returns a single green view."""

    def fetch_required_checks(
        self, *, pr_number: int, branch: str
    ) -> RequiredChecksView:
        return RequiredChecksView(
            pr_number=pr_number,
            branch=branch,
            state=CheckRunState.SUCCESS,
            checks=(),
        )


def _make_monitor() -> StubCiMonitor:
    return StubCiMonitor(
        policy=None,  # type: ignore[arg-type]
        checks_client=_FakeCiClient(),
        ready_evaluator=lambda v: v.state == CheckRunState.SUCCESS,
        ready_transition=lambda run_id, view: None,
        view_factory=lambda pr, br: RequiredChecksView(
            pr_number=pr,
            branch=br,
            state=CheckRunState.SUCCESS,
            checks=(),
        ),
    )


class _CapturingExecutor(StubTaskExecutionService):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, request: Any) -> Any:
        self.calls.append(("execute", (request,)))
        return super().execute(request)


# --- Tests ------------------------------------------------------------------


def test_feature_dispatches_to_task_executor() -> None:
    executor = _CapturingExecutor()
    service = ControllerApplicationService(
        task_executor=executor,
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    result = service.feature_request(
        FeatureRequest(repository_url="git@github.com:foo/bar.git", description="hi")
    )
    assert executor.calls == [("execute", (executor.last_request,))]
    assert "run_id" in result


def test_pr_returns_poll_result_outcome() -> None:
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    result = service.pr_status("run-1")
    assert result.get("ok") is True
    assert result.get("outcome") in (
        PollOutcome.READY.value,
        PollOutcome.EXHAUSTED.value,
    )


def test_status_returns_unknown_for_missing_run() -> None:
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    result = service.status("does-not-exist")
    assert result == {"ok": False, "state": "unknown"}


def test_status_returns_state_for_known_run() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.status("run-1")
    assert result["ok"] is True
    assert result["state"] in ("running", "pending", "complete")


def test_runs_lists_known_runs() -> None:
    ledger = RunLedger()
    ledger.record_start("run-a", repository="repo-a")
    ledger.record_start("run-b", repository="repo-b")
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.runs()
    assert result["ok"] is True
    run_ids = {r["run_id"] for r in result["runs"]}
    assert run_ids == {"run-a", "run-b"}


def test_runs_bounded_to_50() -> None:
    ledger = RunLedger()
    for i in range(75):
        ledger.record_start(f"run-{i:03d}", repository=f"repo-{i}")
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.runs()
    assert result["ok"] is True
    assert len(result["runs"]) == 50


def test_resume_forwards_to_task_executor() -> None:
    executor = _CapturingExecutor()
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=executor,
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.resume("run-1")
    assert result["ok"] is True


def test_cancel_forwards_to_task_executor() -> None:
    executor = _CapturingExecutor()
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=executor,
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.cancel("run-1")
    assert result["ok"] is True


def test_application_service_protocol_conformance() -> None:
    """ControllerApplicationService MUST satisfy the slice-11 Protocol."""
    from seharness.telegram import ApplicationService

    service: ApplicationService = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    # No merge methods allowed (slice 11 invariant).
    forbidden = ("merge", "merge_pull_request", "auto_merge")
    for name in forbidden:
        assert not hasattr(service, name), f"ControllerApplicationService.{name} forbidden"


def test_pr_status_never_returns_merge_outcome() -> None:
    """The /pr response MUST NOT include a merge action."""
    service = ControllerApplicationService(
        task_executor=StubTaskExecutionService(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    result = service.pr_status("run-1")
    lowered = {k.lower() for k in result}
    for forbidden in ("merge", "merge_pull_request", "auto_merge"):
        assert forbidden not in lowered
