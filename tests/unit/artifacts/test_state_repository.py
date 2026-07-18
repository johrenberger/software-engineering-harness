"""RED phase: resumable state repository.

A ``StateRepository`` persists ``WorkflowState`` to disk and can
rehydrate the last valid state to resume an interrupted run. Per
spec \u00a78 and \u00a710:

- state must be persisted atomically
- state must be resumable after interruption
- completed phase artifacts must not be regenerated unless explicitly invalidated
- retry counters must be durable
- duplicate resume does not create duplicate commits or PRs (slice 9
  enforces that; this slice only guarantees that resume reads the
  last checkpoint and is idempotent on the state file itself)

These tests cover:

1. Save and load round-trip
2. Atomic save with crash safety (the file is the last valid state)
3. ``list_runs()`` returns all run IDs under the artifact root
4. ``get_run(run_id)`` raises ``RunNotFoundError`` for unknown IDs
5. ``find_resumable()`` returns runs whose current phase is not
   terminal (i.e. they're mid-flight and could be resumed)
6. Resuming an interrupted run replays the persisted history onto a
   fresh ``WorkflowState`` that has identical transition history
7. The retry counters (task_retries, repair_retries) survive a
   save/load round-trip
8. Two consecutive ``save``s do not leak temp files under normal
   operation
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from seharness.artifacts import store as store_module
from seharness.artifacts.store import RunNotFoundError, StateRepository
from seharness.domain.enums import PhaseName, RunStatus
from seharness.state_machine import WorkflowState


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def repository(tmp_path: Path) -> Iterator[StateRepository]:
    repo = StateRepository(tmp_path)
    yield repo


def _make_state(
    run_id: str = "r-2026-07-18T00-00-00Z",
    phase: PhaseName = PhaseName.INTAKE,
    status: RunStatus = RunStatus.RUNNING,
) -> WorkflowState:
    return WorkflowState(run_id=run_id, current_phase=phase, run_status=status)


# ---------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------
class TestStateRepositorySaveLoad:
    def test_save_persists_state_file(self, repository: StateRepository, tmp_path: Path) -> None:
        state = _make_state()
        repository.save(state)
        path = tmp_path / "r-2026-07-18T00-00-00Z" / "run-state.json"
        assert path.is_file()
        # File must be valid JSON with the same fields.
        loaded = json.loads(path.read_text())
        assert loaded["run_id"] == state.run_id
        assert loaded["current_phase"] == state.current_phase.value
        assert loaded["run_status"] == state.run_status.value

    def test_load_round_trip_returns_equivalent_state(self, repository: StateRepository) -> None:
        state = _make_state(phase=PhaseName.EXECUTION)
        repository.save(state)
        loaded = repository.load(state.run_id)
        assert loaded == state

    def test_history_is_persisted(self, repository: StateRepository) -> None:
        state = _make_state()
        # Walk through several transitions.
        for target in (
            PhaseName.DISCOVERY,
            PhaseName.SPECIFICATION,
            PhaseName.IMPACT,
            PhaseName.PLANNING,
        ):
            state = state.transition_to(target)
        repository.save(state)
        loaded = repository.load(state.run_id)
        assert loaded.history == state.history

    def test_retry_counters_are_durable(self, repository: StateRepository) -> None:
        """The docs are explicit: ``retry counters must be durable``.
        Save, advance task_retries via a fresh state, save again, then
        reload and confirm the counter stuck.
        """
        state = WorkflowState(
            run_id="r-retry",
            current_phase=PhaseName.EXECUTION,
            run_status=RunStatus.RUNNING,
            task_retries=2,
            repair_retries=1,
        )
        repository.save(state)
        loaded = repository.load("r-retry")
        assert loaded.task_retries == 2
        assert loaded.repair_retries == 1


# ---------------------------------------------------------------------
# 2. Atomic save crash safety
# ---------------------------------------------------------------------
class TestStateRepositoryAtomicSave:
    def test_save_uses_atomic_write(
        self, repository: StateRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The repository should delegate to ``atomic_write_json`` (or
        directly use the same atomic-write protocol) so a crash mid-write
        leaves the destination at its prior valid content.

        We simulate this by patching the underlying ``os.replace`` to
        raise during the first call only; a second save should still
        succeed.
        """
        state = _make_state()
        repository.save(state)
        original = (tmp_path / "r-2026-07-18T00-00-00Z" / "run-state.json").read_text()

        # Patch os.replace to raise on the second call (the test writes
        # once via the prior save, then attempts a second save).

        original_replace = os.replace
        raised_once = {"flag": False}

        def maybe_crash(src: str, dst: str) -> None:
            if not raised_once["flag"] and "run-state.json" in str(dst):
                raised_once["flag"] = True
                raise OSError("simulated crash during save")
            return original_replace(src, dst)

        monkeypatch.setattr(store_module.os, "replace", maybe_crash)

        with pytest.raises(OSError):
            repository.save(state)  # this one crashes

        # The destination must still hold the prior valid content.
        path = tmp_path / "r-2026-07-18T00-00-00Z" / "run-state.json"
        assert path.read_text() == original

    def test_save_does_not_leak_temp_files_on_success(
        self, repository: StateRepository, tmp_path: Path
    ) -> None:
        state = _make_state()
        for _ in range(5):
            repository.save(state)
        run_dir = tmp_path / "r-2026-07-18T00-00-00Z"
        # No .tmp.* files should remain under the run directory.
        leaked = list(run_dir.glob(".tmp.*"))
        assert leaked == [], f"unexpected temp leftovers: {leaked}"


# ---------------------------------------------------------------------
# 3. List / lookup
# ---------------------------------------------------------------------
class TestStateRepositoryListing:
    def test_list_runs_returns_all_saved_run_ids(self, repository: StateRepository) -> None:
        repository.save(_make_state(run_id="r-a"))
        repository.save(_make_state(run_id="r-b"))
        repository.save(_make_state(run_id="r-c"))
        ids = set(repository.list_runs())
        assert ids == {"r-a", "r-b", "r-c"}

    def test_list_runs_returns_empty_on_unknown_path(self, repository: StateRepository) -> None:
        assert list(repository.list_runs()) == []

    def test_load_unknown_run_raises(self, repository: StateRepository) -> None:

        with pytest.raises(RunNotFoundError) as ei:
            repository.load("never-saved")
        assert "never-saved" in str(ei.value)


# ---------------------------------------------------------------------
# 4. find_resumable()
# ---------------------------------------------------------------------
class TestFindResumable:
    @pytest.mark.parametrize(
        "phase",
        [
            PhaseName.INTAKE,
            PhaseName.DISCOVERY,
            PhaseName.EXECUTION,
            PhaseName.VALIDATION,
            PhaseName.DELIVERY,
            PhaseName.CI_MONITORING,
            PhaseName.REMEDIATION,
            PhaseName.REVIEW,
            PhaseName.PLANNING,
        ],
    )
    def test_normal_phase_runs_are_resumable(
        self, repository: StateRepository, phase: PhaseName
    ) -> None:
        repository.save(_make_state(run_id=f"r-{phase.value}", phase=phase))
        assert "r-" + phase.value in list(repository.find_resumable())

    @pytest.mark.parametrize(
        "phase",
        [PhaseName.COMPLETED, PhaseName.FAILED, PhaseName.BLOCKED],
    )
    def test_terminal_phase_runs_are_not_resumable(
        self, repository: StateRepository, phase: PhaseName
    ) -> None:
        repository.save(_make_state(run_id=f"r-{phase.value}", phase=phase))
        assert all(rid != "r-" + phase.value for rid in repository.find_resumable())


# ---------------------------------------------------------------------
# 5. Resume from a checkpoint
# ---------------------------------------------------------------------
class TestResumeFromCheckpoint:
    def test_resume_after_interruption_replays_history(self, repository: StateRepository) -> None:
        """A run that crashed at PHASE A, on resume, must reconstruct a
        WorkflowState whose ``current_phase == PHASE A`` and whose
        ``history`` matches what was persisted."""
        state = _make_state()
        for target in (PhaseName.DISCOVERY, PhaseName.SPECIFICATION, PhaseName.IMPACT):
            state = state.transition_to(target)
        repository.save(state)
        # Imagine the controller crashed here. A new process comes
        # along, loads the state, and continues.
        resumed = repository.load(state.run_id)
        assert resumed.current_phase == PhaseName.IMPACT
        assert resumed.history == state.history
        # And the resumed state can continue transitioning.
        next_phase = resumed.transition_to(PhaseName.PLANNING)
        assert next_phase.current_phase == PhaseName.PLANNING

    def test_resume_is_idempotent(self, repository: StateRepository) -> None:
        """Calling ``load`` twice on the same run must produce identical
        state values (slice 9 builds on this for 'no duplicate commits')."""
        state = _make_state(phase=PhaseName.EXECUTION)
        repository.save(state)
        first = repository.load(state.run_id)
        second = repository.load(state.run_id)
        assert first == second
