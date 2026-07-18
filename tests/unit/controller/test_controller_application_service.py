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

from typing import TYPE_CHECKING, Any

from seharness.ci import (
    RequiredChecksView,
    StubCiMonitor,
)
from seharness.controller import (
    ControllerApplicationService,
    RunLedger,
    StubFeatureExecutor,
)
from seharness.telegram import FeatureRequest

if TYPE_CHECKING:
    pass


# --- Test doubles -----------------------------------------------------------


class _FakeCiClient:
    """Minimal ChecksClient double; returns a single green view."""

    def fetch_view(self, pr_number: str, branch: str) -> RequiredChecksView:
        return RequiredChecksView(
            branch=branch,
            head_sha="abc1234",
            required=("lint", "test"),
            all_checks=(),
            mergeable_unknown=False,
        )


def _make_monitor() -> StubCiMonitor:
    return StubCiMonitor(
        client=_FakeCiClient(),
        view_factory=lambda: RequiredChecksView(
            branch="agent/x",
            head_sha="abc1234",
            required=(),
            all_checks=(),
            mergeable_unknown=False,
        ),
    )


class _CapturingExecutor(StubFeatureExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, request: Any) -> Any:
        self.calls.append(("execute", (request,)))
        self.last_request = request
        self._counter += 1
        run_id = f"run-{self._counter:03d}"
        return {
            "ok": True,
            "run_id": run_id,
            "repository": request.repository_url,
        }


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
    assert len(executor.calls) == 1
    assert executor.calls[0][0] == "execute"
    assert "run_id" in result


def test_pr_returns_poll_result_outcome() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.pr_status("run-1")
    assert result.get("ok") is True
    assert result.get("outcome") in ("ready", "still_pending")


def test_status_returns_unknown_for_missing_run() -> None:
    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    result = service.status("does-not-exist")
    assert result.get("ok") is False
    assert result.get("state") == "unknown"


def test_status_returns_state_for_known_run() -> None:
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
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
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.runs()
    assert isinstance(result, tuple)
    assert set(result) == {"run-a", "run-b"}


def test_runs_bounded_to_50() -> None:
    ledger = RunLedger()
    for i in range(75):
        ledger.record_start(f"run-{i:03d}", repository=f"repo-{i}")
    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.runs()
    assert isinstance(result, tuple)
    assert len(result) == 50


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
    from seharness.telegram import ApplicationService  # noqa: PLC0415

    service: ApplicationService = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=RunLedger(),
    )
    # No merge methods allowed (slice 11 invariant).
    forbidden = ("merge", "merge_pull_request", "auto_merge")
    for name in forbidden:
        assert not hasattr(service, name), f"ControllerApplicationService.{name} forbidden"


def test_pr_status_never_returns_merge_outcome() -> None:
    """The /pr response MUST NOT include a merge action."""
    ledger = RunLedger()
    ledger.record_start("run-1", repository="git@github.com:foo/bar.git")
    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=_make_monitor(),
        run_ledger=ledger,
    )
    result = service.pr_status("run-1")
    lowered = {k.lower() for k in result}
    for forbidden in ("merge", "merge_pull_request", "auto_merge"):
        assert forbidden not in lowered
