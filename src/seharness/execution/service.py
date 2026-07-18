"""Task execution service for slice 6.

Per SPEC \u00a7"TDD evidence" and slice 6 RED bullets, the public boundary
is ``TaskExecutionService.execute(plan, task_id, runner)``.

The service:

1. Snapshots the workspace BEFORE running the task
   (``WorkspaceSnapshot.record`` over production paths).
2. Invokes ``runner(red_dir, green_dir)`` \u2014 the runner is responsible
   for actually running pytest and writing the evidence files. The
   runner signature is deliberately minimal so tests can supply
   in-memory stubs.
3. Validates the evidence via ``TaskCompletionValidator.assert_complete``.
4. Reverts unauthorized changes via ``revert_unauthorized``.
5. Persists a ``task-result.json`` summarising the run.

The service does NOT itself run pytest \u2014 the runner contract is the
TDD-loop boundary. Slice 7 will likely introduce a concrete
``PytestRunner``; slice 9 will wire the orchestrator.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from seharness.artifacts.traceability import Plan
from seharness.execution.completion import (
    CompletionRejection,
    TaskCompletionValidator,
)
from seharness.execution.evidence import TaskEvidenceLayout
from seharness.execution.paths import (
    AllowedPaths,
    PathAuthorizationRule,
    ProhibitedPaths,
)
from seharness.execution.workspace import (
    WorkspaceSnapshot,
    revert_unauthorized,
)


class TaskEvidenceError(RuntimeError):
    """Raised when the runner failed to produce the required evidence."""


class TaskNotFoundError(KeyError):
    """Raised when ``task_id`` is not in the supplied plan."""

    def __init__(self, plan_id: str, task_id: str) -> None:
        # super().__init__ first per CPython 3.13 peephole trap.
        super().__init__(f"task {task_id!r} not found in plan {plan_id!r}")
        self.plan_id = plan_id
        self.task_id = task_id


class _TaskResultModel(BaseModel):
    """Persisted shape of task-result.json on disk.

    ``ConfigDict(extra=\"forbid\", frozen=True)`` so accidental additions
    are caught at write time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    plan_id: str
    completed: bool
    evidence_root: str
    red_exit_code: int
    green_exit_code: int
    violations: tuple[str, ...]
    completed_at: str


@dataclass(frozen=True)
class TaskResult:
    """In-memory result of a task execution. Persisted via ``task-result.json``."""

    task_id: str
    completed: bool
    evidence_root: Path
    red_exit_code: int
    green_exit_code: int
    violations: tuple[str, ...] = ()

    def to_model(self, *, plan_id: str) -> _TaskResultModel:
        return _TaskResultModel(
            task_id=self.task_id,
            plan_id=plan_id,
            completed=self.completed,
            evidence_root=str(self.evidence_root),
            red_exit_code=self.red_exit_code,
            green_exit_code=self.green_exit_code,
            violations=self.violations,
            completed_at=datetime.now(UTC).isoformat(),
        )


# Runner contract: takes (red_dir, green_dir), writes evidence files.
Runner = Callable[[Path, Path], None]


@dataclass(frozen=True)
class TaskExecutionService:
    """Public boundary: execute a single task from a Plan."""

    repo_root: Path
    execution_root: Path

    def execute(
        self,
        *,
        plan: Plan,
        task_id: str,
        runner: Runner,
    ) -> TaskResult:
        """Run a single task through the TDD evidence loop.

        Returns a ``TaskResult`` and writes ``task-result.json`` to
        ``execution/<task_id>/task-result.json``.

        Raises:
            TaskNotFoundError: ``task_id`` not in ``plan``.
            TaskEvidenceError: runner produced incomplete evidence.
            CompletionRejection: evidence exists but does not satisfy
                the completion gates.
        """
        task = self._lookup_task(plan, task_id)
        layout = TaskEvidenceLayout(task_id=task_id, root=self.execution_root)

        # 1. Snapshot the workspace.
        snapshot = WorkspaceSnapshot(root=self.repo_root, captured_at=datetime.now(UTC))
        self._take_snapshot(snapshot)

        # 2. Run the runner.
        layout.task_dir.mkdir(parents=True, exist_ok=True)
        runner(layout.red_dir, layout.green_dir)

        # 3. Validate evidence.
        try:
            TaskCompletionValidator().assert_complete(layout)
        except CompletionRejection as e:
            raise TaskEvidenceError(str(e)) from e

        # 4. Revert unauthorized changes.
        # NOTE: Task does not yet have a ``prohibited_paths`` field (reserved
        # for slice 6); for now we pass an empty prohibition list.
        rule = PathAuthorizationRule(
            task_id=task_id,
            allowed_paths=AllowedPaths(task.allowed_paths),
            prohibited_paths=ProhibitedPaths(()),
        )
        reverted = revert_unauthorized(self.repo_root, snapshot, rule)

        # 5. Build and persist TaskResult.
        red_exit = self._read_exit_code(layout.red_dir / "result.json")
        green_exit = self._read_exit_code(layout.green_dir / "result.json")
        result = TaskResult(
            task_id=task_id,
            completed=True,
            evidence_root=layout.task_dir,
            red_exit_code=red_exit,
            green_exit_code=green_exit,
            violations=tuple(str(p) for p in reverted),
        )
        # Persist via Pydantic's model_dump_json for stable shape.
        model = result.to_model(plan_id=plan.plan_id)
        layout.task_result_path.write_text(model.model_dump_json(indent=2) + "\n")
        return result

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _lookup_task(plan: Plan, task_id: str):  # type: ignore[no-untyped-def]
        for task in plan.tasks:
            if task.task_id == task_id:
                return task
        raise TaskNotFoundError(plan.plan_id, task_id)

    def _take_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """Snapshot every file under ``self.repo_root`` (excluding
        ``.venv`` / ``.git`` / ``__pycache__``)."""
        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo_root)
            parts = rel.parts
            if any(p.startswith(".") for p in parts[:1]):
                # Skip dot-dirs like .git, .venv at the top level.
                continue
            if any(p == "__pycache__" for p in parts):
                continue
            if any(p == ".openclaw-runs" for p in parts):
                continue
            snapshot.record(
                path,
                mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                size=path.stat().st_size,
            )

    @staticmethod
    def _read_exit_code(result_json: Path) -> int:
        payload = json.loads(result_json.read_text())
        return int(payload.get("exit_code", -1))


__all__ = [
    "Runner",
    "TaskEvidenceError",
    "TaskExecutionService",
    "TaskNotFoundError",
    "TaskResult",
]
