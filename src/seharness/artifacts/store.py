"""Atomic artifact storage primitives and ``StateRepository``.

This module owns filesystem I/O for the artifact store. The state
machine and other modules in slice 2 must NOT write to disk directly;
they go through ``atomic_write_json`` or ``StateRepository`` so the
crash semantics stay uniform across the codebase.

Crash semantics (per spec \u00a78):

- ``atomic_write_json`` writes to a temp file in the SAME directory as
  the destination, then ``os.replace``s the temp into place.
- ``os.replace`` is atomic on POSIX when both paths are on the same
  filesystem; staying in the same directory guarantees that.
- If anything between the temp-file open and the rename raises, the
  destination is left at its prior valid content (or absent).
- The temp file is removed on success.

The Pydantic ``WorkflowStateModel`` wraps the runtime ``WorkflowState``
dataclass for serialization. Downstream slices that need to read or
write state go through ``StateRepository``; the dataclass and the
Pydantic model never cross the wire directly.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from seharness.domain.enums import PhaseName, RunStatus
from seharness.state_machine import TERMINAL_PHASES, WorkflowState


class RunNotFoundError(KeyError):
    """Raised when ``StateRepository.load`` cannot find the run on disk.

    Subclasses ``KeyError`` so generic ``except KeyError`` blocks
    continue to work; the string conversion mentions the missing run id.
    """

    def __init__(self, run_id: str, root: Path) -> None:
        # NOTE: super().__init__ must run BEFORE the self.* assignments.
        # CPython's peephole optimizer emits ``LOAD_CONST None`` for
        # ``self.x = x`` when the local ``x`` is only read in the
        # *next* statement (the super call), which silently nulls
        # the attribute. Calling super first keeps the locals alive.
        super().__init__(f"run {run_id!r} not found under {root}")
        self.run_id = run_id
        self.root = root


# ---------------------------------------------------------------------
# Pydantic model used for serialization.
# ---------------------------------------------------------------------
class WorkflowStateModel(BaseModel):
    """JSON-serializable counterpart of ``WorkflowState``.

    The dataclass lives in ``seharness.state_machine`` and owns the
    runtime invariants (frozen, no-IO). This model owns nothing of
    substance; it is a DTO for ``StateRepository``.

    Unknown top-level keys are forbidden (``extra=\"forbid\"``) so a
    malformed run-state.json is caught loudly instead of silently
    carried forward into a corrupted run.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    current_phase: PhaseName
    run_status: RunStatus
    task_retries: int = 0
    repair_retries: int = 0
    history: tuple[tuple[PhaseName, PhaseName], ...] = ()
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_state(cls, state: WorkflowState) -> WorkflowStateModel:
        return cls(
            run_id=state.run_id,
            current_phase=state.current_phase,
            run_status=state.run_status,
            task_retries=state.task_retries,
            repair_retries=state.repair_retries,
            history=state.history,
            updated_at=state.updated_at,
        )

    def to_state(self) -> WorkflowState:
        # Re-construct the immutable dataclass. ``frozen=True`` means
        # the caller can't subtly mutate this; the only way to advance
        # the run is through ``transition_to``.
        return WorkflowState(
            run_id=self.run_id,
            current_phase=self.current_phase,
            run_status=self.run_status,
            task_retries=self.task_retries,
            repair_retries=self.repair_retries,
            history=self.history,
            updated_at=self.updated_at,
        )


# ---------------------------------------------------------------------
# Atomic write primitive. Lives here so the rest of slice 2 depends
# on a single I/O boundary.
# ---------------------------------------------------------------------
def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    indent: int = 2,
) -> None:
    """Atomically write ``payload`` (encoded as JSON) to ``path``.

    See module docstring for crash semantics.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp.",
        suffix=".json",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=indent, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------
# State repository: persists run-state.json per run directory.
# ---------------------------------------------------------------------
class StateRepository:
    """File-backed storage of per-run WorkflowState.

    Layout::

        <root>/
            <run-id>/
                run-state.json

    The directory per run enables future slices to add per-phase
    artifacts (intake/, discovery/, ...) without colliding on filenames.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _state_path(self, run_id: str) -> Path:
        return self._root / run_id / "run-state.json"

    # ----- write -----
    def save(self, state: WorkflowState) -> None:
        """Persist ``state`` atomically at ``run-state.json``."""
        model = WorkflowStateModel.from_state(state)
        # mode='json' is Pydantic v2's safe JSON serializer that honors
        # non-trivial types like ``datetime`` and ``StrEnum``.
        atomic_write_json(self._state_path(state.run_id), model.model_dump(mode="json"))

    # ----- read -----
    def load(self, run_id: str) -> WorkflowState:
        """Load ``run_id``'s persisted state.

        Raises ``RunNotFoundError`` if the file does not exist.
        """
        path = self._state_path(run_id)
        if not path.is_file():
            raise RunNotFoundError(run_id, self._root)
        raw = json.loads(path.read_text(encoding="utf-8"))
        model = WorkflowStateModel.model_validate(raw)
        return model.to_state()

    # ----- listing -----
    def list_runs(self) -> Iterable[str]:
        """Return all run IDs that have a run-state.json on disk."""
        if not self._root.is_dir():
            return iter(())
        return sorted(
            child.name
            for child in self._root.iterdir()
            if child.is_dir() and (child / "run-state.json").is_file()
        )

    def find_resumable(self) -> Iterable[str]:
        """Return run IDs whose current phase is not terminal.

        A resumable run is mid-flight: a future controller can pick it
        up and ``load`` + ``transition_to`` further.
        """
        resumable: list[str] = []
        for run_id in self.list_runs():
            state = self.load(run_id)
            if state.current_phase not in TERMINAL_PHASES:
                resumable.append(run_id)
        return resumable
