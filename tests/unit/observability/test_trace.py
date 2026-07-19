"""RED tests for ``seharness.observability.trace``.

The ``Trace`` module writes a structured, append-only, JSONL-encoded
event log to disk for observability and incident response. Every
event is redacted before write so secrets never leak to disk.

Stories E5 (Trace model) + E6 (persistence).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seharness.observability.redactor import REDACTION_SENTINEL
from seharness.observability.trace import (
    ArtifactProduced,
    PhaseCompleted,
    PhaseFailed,
    PhaseStarted,
    TraceEvent,
    TraceWriter,
)

# ---------------------------------------------------------------------------
# TraceEvent model
# ---------------------------------------------------------------------------


class TestPhaseStarted:
    def test_minimal(self) -> None:
        e = PhaseStarted(run_id="orch-abc12345", phase="planning", attempt=0)
        assert e.run_id == "orch-abc12345"
        assert e.phase == "planning"
        assert e.attempt == 0
        assert e.kind == "phase_started"
        assert e.timestamp > 0

    def test_frozen(self) -> None:
        e = PhaseStarted(run_id="orch-abc", phase="planning")
        with pytest.raises((AttributeError, ValueError)):
            e.phase = "validation"  # type: ignore[misc]


class TestPhaseCompleted:
    def test_minimal(self) -> None:
        e = PhaseCompleted(run_id="orch-abc", phase="planning", outcome="ok")
        assert e.outcome == "ok"
        assert e.kind == "phase_completed"

    def test_with_artifact_paths(self) -> None:
        e = PhaseCompleted(
            run_id="orch-abc",
            phase="specification",
            outcome="ok",
            artifact_paths=("specification.json",),
        )
        assert e.artifact_paths == ("specification.json",)


class TestPhaseFailed:
    def test_minimal(self) -> None:
        e = PhaseFailed(
            run_id="orch-abc",
            phase="validation",
            outcome="failed",
            error="exit code 1",
        )
        assert e.error == "exit code 1"
        assert e.kind == "phase_failed"


class TestArtifactProduced:
    def test_minimal(self) -> None:
        e = ArtifactProduced(
            run_id="orch-abc",
            phase="specification",
            path="specification.json",
            artifact_kind="spec",
        )
        assert e.kind == "artifact_produced"
        assert e.path == "specification.json"
        assert e.artifact_kind == "spec"


class TestTraceEventDiscrimination:
    """TraceEvent is a tagged union over ``kind``."""

    def test_phase_started_event_kind(self) -> None:
        e = PhaseStarted(run_id="orch-abc", phase="planning")
        assert e.kind == "phase_started"

    def test_phase_completed_event_kind(self) -> None:
        e = PhaseCompleted(run_id="orch-abc", phase="planning", outcome="ok")
        assert e.kind == "phase_completed"

    def test_phase_failed_event_kind(self) -> None:
        e = PhaseFailed(run_id="orch-abc", phase="validation", outcome="failed", error="x")
        assert e.kind == "phase_failed"

    def test_artifact_produced_event_kind(self) -> None:
        e = ArtifactProduced(run_id="orch-abc", phase="spec", path="x.json", artifact_kind="spec")
        assert e.kind == "artifact_produced"

    def test_to_dict_round_trip(self) -> None:
        e = PhaseStarted(run_id="orch-abc", phase="planning")
        d = e.to_dict()
        # round-trip via TraceEvent.parse
        e2 = TraceEvent.from_dict(d)
        assert e2 == e


# ---------------------------------------------------------------------------
# Trace writer (file-backed, append-only JSONL)
# ---------------------------------------------------------------------------


class TestTraceWriterAppend:
    """The writer appends one JSON object per line, with fsync per write."""

    def test_writes_to_file(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw = TraceWriter(path=path)
        tw.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        tw.emit(PhaseCompleted(run_id="orch-abc", phase="planning", outcome="ok"))
        tw.close()
        content = path.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        # Each line is a valid JSON object
        for ln in lines:
            d = json.loads(ln)
            assert "kind" in d
            assert "run_id" in d

    def test_writes_one_json_object_per_line(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw = TraceWriter(path=path)
        for i in range(5):
            tw.emit(PhaseStarted(run_id="orch-abc", phase=f"phase-{i}"))
        tw.close()
        content = path.read_text()
        # No embedded newlines within a JSON object.
        assert content.count("\n{") >= 4

    def test_appends_across_writers(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw1 = TraceWriter(path=path)
        tw1.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        tw1.close()
        # New writer opens same path in append mode.
        tw2 = TraceWriter(path=path, append=True)
        tw2.emit(PhaseCompleted(run_id="orch-abc", phase="planning", outcome="ok"))
        tw2.close()
        content = path.read_text()
        assert len(content.strip().split("\n")) == 2


class TestTraceWriterRedaction:
    """Secret-looking fields are scrubbed before write."""

    def test_secret_in_error_field_redacted(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw = TraceWriter(path=path)
        tw.emit(
            PhaseFailed(
                run_id="orch-abc",
                phase="validation",
                outcome="failed",
                error="auth failed: token=ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            )
        )
        tw.close()
        content = path.read_text()
        assert "ghp_abcdef" not in content
        assert REDACTION_SENTINEL in content

    def test_secret_in_artifact_path_redacted(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw = TraceWriter(path=path)
        tw.emit(
            ArtifactProduced(
                run_id="orch-abc",
                phase="planning",
                path="api_key=sk-abcdef1234567890abcdef1234567890.json",
                artifact_kind="plan",
            )
        )
        tw.close()
        content = path.read_text()
        assert "sk-abcdef1234567890" not in content


class TestTraceWriterClose:
    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        tw = TraceWriter(path=path)
        tw.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        tw.close()
        tw.close()  # second close must not raise

    def test_context_manager(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        with TraceWriter(path=path) as tw:
            tw.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        content = path.read_text()
        assert len(content.strip().split("\n")) == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "trace.jsonl"
        tw = TraceWriter(path=path)
        tw.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        tw.close()
        assert path.exists()


# ---------------------------------------------------------------------------
# Trace (alias / convenience constructor)
# ---------------------------------------------------------------------------


class TestTraceFactory:
    """``Trace.for_run(run_id, run_dir)`` returns a TraceWriter at the
    canonical path ``<run_dir>/trace.jsonl``."""

    def test_for_run_default_path(self, tmp_path: Path) -> None:
        from seharness.observability.trace import Trace as TraceFactory

        trace = TraceFactory.for_run(run_id="orch-abc", run_dir=tmp_path)
        try:
            assert trace.path == tmp_path / "trace.jsonl"
            trace.emit(PhaseStarted(run_id="orch-abc", phase="planning"))
        finally:
            trace.close()
        assert (tmp_path / "trace.jsonl").exists()
