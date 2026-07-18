"""RED tests for behavior 04 — Baseline recorder.

The baseline recorder persists the *last-known* validation status of a
repository to ``<run-dir>/.baseline/<gate>.json``. It does **not** run
subprocesses; it only writes / reads the JSON snapshots that slice 7
produces.

The recorder must:

* write atomically (using slice 2's :func:`atomic_write_json`)
* encode status as one of ``pass | fail | partial | unknown``
* record timestamp (ISO-8601), commit SHA, and per-gate summaries
* round-trip cleanly (load returns the same payload)
* tolerate a missing baseline (returns empty mapping, not an error)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from seharness.repository import conventions
from seharness.repository.conventions import BaselineRecorder, BaselineSnapshot
from seharness.repository.discovery import BaselineStatus


def _baseline_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".baseline"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestBaselineRecorderWrite:
    def test_writes_snapshot_file(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        snap = BaselineSnapshot(
            gate="test",
            status=BaselineStatus.PASS,
            captured_at=datetime(2026, 7, 18, 17, 0, 0),
            commit="abc123",
            duration_seconds=12.5,
            summary="239 passed",
        )
        rec.write(snap)
        assert (_baseline_dir(tmp_path) / "test.json").exists()

    def test_writes_valid_json(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.FAIL,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="abc",
                duration_seconds=5.0,
                summary="3 failed",
            )
        )
        payload = json.loads((_baseline_dir(tmp_path) / "test.json").read_text())
        assert payload["status"] == "fail"
        assert payload["commit"] == "abc"


class TestBaselineRecorderRead:
    def test_round_trip(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        original = BaselineSnapshot(
            gate="lint",
            status=BaselineStatus.PARTIAL,
            captured_at=datetime(2026, 7, 18, 17, 0, 0),
            commit="def",
            duration_seconds=0.5,
            summary="2 warnings",
        )
        rec.write(original)
        loaded = rec.read("lint")
        assert loaded == original

    def test_missing_baseline_returns_none(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        assert rec.read("test") is None

    def test_load_all_returns_all_snapshots(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="x",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        rec.write(
            BaselineSnapshot(
                gate="lint",
                status=BaselineStatus.FAIL,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="x",
                duration_seconds=1.0,
                summary="err",
            )
        )
        all_snaps = rec.load_all()
        assert set(all_snaps) == {"test", "lint"}


class TestBaselineRecorderAggregates:
    def test_aggregate_pass_when_all_pass(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        rec.write(
            BaselineSnapshot(
                gate="lint",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        assert rec.aggregate_status() == BaselineStatus.PASS

    def test_aggregate_fail_if_any_fail(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        rec.write(
            BaselineSnapshot(
                gate="lint",
                status=BaselineStatus.FAIL,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="err",
            )
        )
        assert rec.aggregate_status() == BaselineStatus.FAIL

    def test_aggregate_partial_if_any_partial_and_none_fail(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.PARTIAL,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="warn",
            )
        )
        assert rec.aggregate_status() == BaselineStatus.PARTIAL

    def test_aggregate_unknown_when_no_snapshots(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        assert rec.aggregate_status() == BaselineStatus.UNKNOWN


class TestBaselineRecorderAtomic:
    def test_uses_atomic_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The recorder must delegate to slice 2's atomic_write_json."""
        called = []

        def spy(path: Path, payload: object, **kw: object) -> None:
            called.append(path)

        monkeypatch.setattr(conventions, "atomic_write_json", spy)
        rec = BaselineRecorder(_baseline_dir(tmp_path))
        rec.write(
            BaselineSnapshot(
                gate="test",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        assert called, "recorder must call atomic_write_json"
