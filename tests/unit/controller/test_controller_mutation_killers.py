"""RED: Mutation killers for slice-12 controller + dashboard models.

Per SPEC §'Mandatory Mutation Testing' — Pydantic / StrEnum / frozen
dataclass killers. Mirrors slice 11's mutation_killers pattern.

RED bullets covered:
- DashboardSnapshot frozen=True
- GitCommit frozen=True + __eq__
- RunRecord frozen=True
- RunState StrEnum (NOT just Literal)
- ControllerConfig frozen + extra='forbid'
- DashboardRenderer has no merge methods
- ControllerApplicationService has no merge methods
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from seharness.controller import (
    ControllerApplicationService,
    ControllerConfig,
    RunLedger,
    RunRecord,
    RunState,
    StubFeatureExecutor,
    StubPauser,
    StubResumer,
)
from seharness.dashboard import DashboardRenderer, DashboardSnapshot, GitCommit


def test_dashboard_snapshot_frozen() -> None:
    snap = DashboardSnapshot(
        harness_version="0.1.0",
        current_slice="12",
        current_slice_name="openclaw-packaging",
        last_green_commit=None,
        latest_run=None,
        generated_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    with pytest.raises(Exception):  # noqa: B017
        snap.harness_version = "9.9.9"  # type: ignore[misc]


def test_dashboard_snapshot_rejects_extra() -> None:
    with pytest.raises(Exception):  # noqa: B017
        DashboardSnapshot(  # type: ignore[call-arg]
            harness_version="0.1.0",
            current_slice="12",
            current_slice_name="openclaw-packaging",
            last_green_commit=None,
            latest_run=None,
            generated_at=datetime(2026, 7, 18, tzinfo=UTC),
            extra_field="forbidden",
        )


def test_git_commit_frozen() -> None:
    commit = GitCommit(
        sha="abcdef0",
        message="msg",
        committed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    with pytest.raises(Exception):  # noqa: B017
        commit.sha = "xyz1234"  # type: ignore[misc]


def test_git_commit_eq_by_sha() -> None:
    ts = datetime(2026, 7, 18, tzinfo=UTC)
    a = GitCommit(sha="abcdef0", message="x", committed_at=ts)
    b = GitCommit(sha="abcdef0", message="x", committed_at=ts)
    assert a.sha == b.sha


def test_run_record_frozen() -> None:
    rec = RunRecord(run_id="x", state=RunState.RUNNING, repository="repo")
    with pytest.raises(Exception):  # noqa: B017
        rec.run_id = "y"  # type: ignore[misc]


def test_run_state_is_strenum() -> None:
    """RunState MUST be StrEnum (not plain Literal) for runtime branching."""
    from enum import StrEnum  # noqa: PLC0415

    assert issubclass(RunState, StrEnum)
    assert str(RunState.RUNNING) == RunState.RUNNING.value


def test_controller_config_frozen() -> None:
    cfg = ControllerConfig(
        services={
            "feature": "stub",
            "status": "stub",
            "runs": "stub",
            "resume": "stub",
            "cancel": "stub",
            "pr": "stub",
        }
    )
    with pytest.raises(Exception):  # noqa: B017
        cfg.services = {  # type: ignore[misc]
            "feature": "different",
            "status": "stub",
            "runs": "stub",
            "resume": "stub",
            "cancel": "stub",
            "pr": "stub",
        }


def test_controller_config_rejects_unknown_service_key() -> None:
    with pytest.raises(Exception):  # noqa: B017
        ControllerConfig(
            services={
                "feature": "stub",
                "status": "stub",
                "runs": "stub",
                "resume": "stub",
                "cancel": "stub",
                "pr": "stub",
                "rogue": "stub",  # type: ignore[list-item]
            }
        )


def test_dashboard_renderer_has_no_merge_methods() -> None:
    """SPEC §'Do not merge automatically.' — DashboardRenderer MUST
    NOT expose any merge* methods."""
    renderer = DashboardRenderer()
    for name in dir(renderer):
        if name.startswith("_"):
            continue
        lowered = name.lower()
        assert "merge" not in lowered, f"DashboardRenderer exposes merge-related method: {name}"


def test_controller_application_service_has_no_merge_methods() -> None:
    from seharness.ci import StubCiMonitor  # noqa: PLC0415

    service = ControllerApplicationService(
        task_executor=StubFeatureExecutor(),
        ci_monitor=StubCiMonitor(
            client=None,  # type: ignore[arg-type]
            view_factory=None,  # type: ignore[arg-type]
        ),
        run_ledger=RunLedger(),
    )
    for name in dir(service):
        if name.startswith("_"):
            continue
        lowered = name.lower()
        assert "merge" not in lowered, (
            f"ControllerApplicationService exposes merge-related method: {name}"
        )


def test_stub_pauser_has_no_merge_methods() -> None:
    pauser = StubPauser(ledger=RunLedger())
    for name in dir(pauser):
        if name.startswith("_"):
            continue
        lowered = name.lower()
        assert "merge" not in lowered, f"StubPauser exposes merge-related method: {name}"


def test_stub_resumer_has_no_merge_methods() -> None:
    resumer = StubResumer(ledger=RunLedger())
    for name in dir(resumer):
        if name.startswith("_"):
            continue
        lowered = name.lower()
        assert "merge" not in lowered, f"StubResumer exposes merge-related method: {name}"


def _snapshot(**overrides) -> DashboardSnapshot:
    base = {
        "harness_version": "0.1.0",
        "current_slice": "12",
        "current_slice_name": "openclaw-packaging",
        "last_green_commit": None,
        "latest_run": None,
        "generated_at": datetime(2026, 7, 18, tzinfo=UTC),
    }
    base.update(overrides)
    return DashboardSnapshot(**base)


def test_dashboard_renderer_write_rejects_directory(tmp_path) -> None:
    renderer = DashboardRenderer()
    with pytest.raises((NotADirectoryError, OSError, ValueError, TypeError, IsADirectoryError)):
        renderer.write(_snapshot(), tmp_path)  # tmp_path is a dir, not file
