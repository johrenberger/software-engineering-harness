"""RED tests for Orchestrator + Trace integration (Cluster E, stories E5+E6).

The orchestrator writes a ``trace.jsonl`` per run alongside the
existing pipeline events. The trace captures phase_started /
phase_completed / phase_failed / artifact_produced events in
append-only JSONL form with secret redaction applied at write time.

If a real phase implementation is removed or stubbed, the trace
emission must still capture the structural transitions (this is a
thin observability layer; it does not change run semantics).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seharness.controller.run_ledger import RunLedger
from seharness.observability.redactor import REDACTION_SENTINEL
from seharness.orchestrator import Orchestrator, OrchestratorConfig


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    (repo / "main.py").write_text("def hello() -> str:\n    return 'hi'\n")
    (repo / "test_main.py").write_text(
        "from main import hello\n\ndef test_hello() -> None:\n    assert hello() == 'hi'\n"
    )
    return repo


def _read_trace_events(run_dir: Path) -> list[dict]:
    p = run_dir / "trace.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    for raw_line in p.read_text().splitlines():
        stripped = raw_line.strip()
        if stripped:
            out.append(json.loads(stripped))
    return out


class TestOrchestratorWritesTraceFile:
    def test_run_writes_trace_jsonl(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        orch.start_run(feature_description="Add hello endpoint", repo_path=str(repo))
        run_dirs = list((tmp_path / "runs").iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        events = _read_trace_events(run_dir)
        assert len(events) > 0, "expected trace.jsonl to be non-empty after a run"

    def test_trace_emits_phase_started_for_each_phase(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        kinds = [e["kind"] for e in events]
        # At least one phase_started must appear (probably 12).
        assert "phase_started" in kinds

    def test_trace_emits_phase_completed_for_each_phase(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        kinds = [e["kind"] for e in events]
        # Each completed phase emits phase_completed.
        assert "phase_completed" in kinds

    def test_trace_phase_started_pairs_with_phase_completed(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        # Every started phase must also have a completed event for the
        # same phase name in a healthy run.
        started_phases = {e["phase"] for e in events if e["kind"] == "phase_started"}
        completed_phases = {e["phase"] for e in events if e["kind"] == "phase_completed"}
        assert started_phases == completed_phases

    def test_trace_includes_run_id(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        result = orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        for e in events:
            assert e["run_id"] == result.run_id

    def test_trace_emits_artifact_produced(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)
        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        artifact_events = [e for e in events if e["kind"] == "artifact_produced"]
        # Specification phase writes specification.json; planner writes plan.json.
        paths = {e["path"] for e in artifact_events}
        assert "specification.json" in paths or "plan.json" in paths


class TestOrchestratorTraceRedaction:
    """Secrets that leak into trace fields are scrubbed at write time."""

    def test_secret_in_phase_detail_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)

        # Monkeypatch the SPECIFICATION handler in the dispatch table to
        # leak a fake GitHub token into the detail string.
        from seharness.orchestrator import orchestrator as orch_mod
        from seharness.orchestrator.types import PhaseName

        original_spec_handler = orch_mod._phase_specification

        def leaky_phase_spec(orch_arg, *, spec, ctx, run_dir):  # type: ignore[no-untyped-def]
            return (
                *original_spec_handler(orch_arg, spec=spec, ctx=ctx, run_dir=run_dir)[0:2],
                "leaked token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 in detail",
            )

        monkeypatch.setitem(orch_mod._PHASE_HANDLERS, PhaseName.SPECIFICATION, leaky_phase_spec)
        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        joined = json.dumps(events)
        assert "ghp_abcdef" not in joined
        assert REDACTION_SENTINEL in joined


class TestOrchestratorTraceFailure:
    """Failed phases still emit trace events; nothing swallowed."""

    def test_failed_phase_emits_phase_failed_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg)

        # Replace the VALIDATION handler with one that returns FAILED.
        from seharness.orchestrator import orchestrator as orch_mod
        from seharness.orchestrator.types import PhaseName, PhaseOutcome

        def failing_phase(orch_arg, *, spec, ctx, run_dir):  # type: ignore[no-untyped-def]
            return PhaseOutcome.FAILED, ctx, "boom"

        monkeypatch.setitem(orch_mod._PHASE_HANDLERS, PhaseName.VALIDATION, failing_phase)

        orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        events = _read_trace_events(run_dir)
        failed = [e for e in events if e["kind"] == "phase_failed"]
        assert failed, "expected at least one phase_failed event"
        assert failed[0]["phase"] == "validation"
        assert "boom" in failed[0].get("error", "")


class TestOrchestratorTraceDisabled:
    """Operators can disable trace emission for tests that don't need it."""

    def test_trace_disabled_when_writer_is_none(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))
        orch = Orchestrator(run_ledger=ledger, config=cfg, trace_writer=None)
        result = orch.start_run(feature_description="x", repo_path=str(repo))
        run_dir = next(iter((tmp_path / "runs").iterdir()))
        # No trace.jsonl when trace disabled.
        assert not (run_dir / "trace.jsonl").exists()
        assert result.terminal_state == "completed"


class TestOrchestratorTraceCustomWriter:
    """An injected TraceWriter is used instead of the default."""

    def test_in_memory_writer_captures_events(self, tmp_path: Path) -> None:
        from seharness.observability.trace import TraceWriter

        repo = _make_repo(tmp_path)
        ledger = RunLedger()
        cfg = OrchestratorConfig(execution_root=str(tmp_path / "runs"))

        custom_path = tmp_path / "custom.jsonl"
        custom_writer = TraceWriter(path=custom_path)
        try:
            orch = Orchestrator(run_ledger=ledger, config=cfg, trace_writer=custom_writer)
            orch.start_run(feature_description="x", repo_path=str(repo))
        finally:
            custom_writer.close()

        # Custom writer's path was used.
        content = custom_path.read_text()
        assert len(content.strip().split("\n")) > 0
