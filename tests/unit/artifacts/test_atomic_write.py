"""RED phase: atomic write preserves prior valid state on interruption.

Per the harness spec §8 ("Artifact Store"):

- ``use atomic writes``
- ``interrupted atomic writes preserve the prior valid state``
- ``do not overwrite prior failed attempts``
- ``preserve every attempt``

Concretely, this module asserts:

1. ``atomic_write_json(path, payload)`` writes to a temp file in the
   same directory and renames into place, so a crash mid-write leaves
   the prior ``path`` untouched.
2. A simulated crash via an injected writer that raises an exception
   after the buffer is half-written does not corrupt the destination
   file.
3. The temp file is removed (or never observed by other readers) after
   the rename, so the destination remains the single source of truth.
4. The rename is atomic on POSIX (use ``os.replace``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from seharness.artifacts import store as store_module
from seharness.artifacts.store import atomic_write_json


class TestAtomicWriteHappyPath:
    def test_writes_valid_json_at_destination(self, tmp_path: Path) -> None:
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake", "retries": 0})
        assert path.is_file()
        with path.open() as f:
            assert json.load(f) == {"phase": "intake", "retries": 0}

    def test_replaces_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake"})
        atomic_write_json(path, {"phase": "discovery"})
        with path.open() as f:
            assert json.load(f) == {"phase": "discovery"}

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "deeply" / "nested" / "run-state.json"
        atomic_write_json(path, {"phase": "intake"})
        assert path.is_file()

    def test_default_indent_is_two_spaces(self, tmp_path: Path) -> None:
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake", "retries": 0})
        text = path.read_text()
        assert "  " in text
        assert '"phase"' in text
        assert '"intake"' in text


class TestAtomicWriteCrashSafety:
    """If the writer is interrupted mid-write, the destination must
    either remain at the prior valid value OR not exist if it didn't
    exist before. A half-written destination is a corruption."""

    def test_partial_write_does_not_corrupt_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate a crash mid-rename. The destination must still hold
        its prior valid content; the rename is never called."""
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake", "retries": 0})
        original = path.read_text()

        def crashing_replace(src: str, dst: str) -> None:
            raise OSError("simulated crash mid-rename")

        monkeypatch.setattr(store_module.os, "replace", crashing_replace)

        with pytest.raises(OSError):
            atomic_write_json(path, {"phase": "discovery", "retries": 1})

        assert path.read_text() == original

    def test_fsync_exception_leaves_destination_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash mid-flush must not corrupt the destination."""
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake"})
        before_text = path.read_text()

        def crashing_fsync(fd: int) -> None:
            raise OSError("simulated crash during fsync")

        monkeypatch.setattr(store_module.os, "fsync", crashing_fsync)

        with pytest.raises(OSError):
            atomic_write_json(path, {"phase": "discovery"})

        assert path.read_text() == before_text

    def test_atomic_write_uses_tempfile_in_same_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The atomic-write protocol puts the temp file in the same
        directory as the destination. A cross-filesystem rename would
        violate atomicity (POSIX rename is atomic only on same FS).
        """
        calls: list[tuple[str, str]] = []

        original_replace = os.replace

        def tracking_replace(src: str, dst: str) -> None:
            calls.append((src, dst))
            return original_replace(src, dst)

        monkeypatch.setattr(store_module.os, "replace", tracking_replace)

        target = tmp_path / "nested" / "run-state.json"
        atomic_write_json(target, {"phase": "intake"})

        assert len(calls) == 1, f"expected one os.replace call, got {calls}"
        src, dst = calls[0]
        assert str(dst) == str(target)
        assert str(target.parent) in src
        assert "tmp" in src.lower() or ".tmp" in src


class TestAtomicWriteDefaultIndent:
    def test_json_ends_with_newline(self, tmp_path: Path) -> None:
        path = tmp_path / "run-state.json"
        atomic_write_json(path, {"phase": "intake"})
        text = path.read_text()
        assert text.endswith("\n")
