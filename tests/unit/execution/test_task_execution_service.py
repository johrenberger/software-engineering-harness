"""RED \u2014 Slice 6 task execution service.

The TaskExecutionService glues together:
- ``WorkspaceSnapshot`` (slice 6)
- ``TaskEvidenceLayout`` (slice 6)
- ``TaskCompletionValidator`` (slice 6)
- ``PathAuthorizationRule`` (slice 6)
- existing ``Plan`` / ``Task`` artifacts from slice 5

It is the public boundary used by the orchestrator (slice 9). For
slice 6 we ship:
- ``execute_task(plan, task_id, runner)`` \u2014 takes a snapshot,
  invokes the runner (a callable that runs the actual TDD loop),
  validates the resulting RED/GREEN evidence, reverts unauthorized
  changes, and returns a TaskResult.

The runner interface is deliberately minimal so slice 6 can be tested
without real subprocess calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest


def _stub_runner(red_dir: Path, green_dir: Path) -> Callable[[Path, Path], None]:
    """Build a runner that writes minimal RED+GREEN evidence."""

    def runner(r: Path, g: Path) -> None:
        for d in (r, g):
            d.mkdir(parents=True, exist_ok=True)
            (d / "command.txt").write_text("pytest tests/unit/foo.py --no-cov -q\n")
            (d / "stdout.txt").write_text("")
            (d / "stderr.txt").write_text("")
        (r / "result.json").write_text(
            json.dumps(
                {
                    "phase": "red",
                    "exit_code": 1,
                    "duration_s": 0.1,
                    "failure_kind": "expected_failure",
                    "failure_reason": "AssertionError",
                    "test_id": "tests/unit/foo.py::test_x",
                    "command": "pytest tests/unit/foo.py --no-cov -q",
                }
            )
            + "\n"
        )
        (g / "result.json").write_text(
            json.dumps(
                {
                    "phase": "green",
                    "exit_code": 0,
                    "duration_s": 0.5,
                    "test_id": "tests/unit/foo.py::test_x",
                    "command": "pytest tests/unit/foo.py --no-cov -q",
                    "covered_tests": ["tests/unit/foo.py::test_x"],
                    "required_tests": ["tests/unit/foo.py::test_x"],
                }
            )
            + "\n"
        )

    return runner


@pytest.fixture
def fake_plan() -> object:
    """Build a real slice-5 ``Plan`` with one task."""
    from seharness.artifacts.traceability import (
        Plan,
        RequirementTrace,
        Task,
    )
    from seharness.domain.requirements import (
        FunctionalRequirementId,
        ScenarioId,
    )

    return Plan(
        plan_id="P-1",
        tasks=(
            Task(
                task_id="T-1",
                summary="Add foo()",
                allowed_paths=("src/seharness/",),
                prohibited_paths=(),
                validation_commands=("pytest tests/unit/foo.py --no-cov -q",),
                requirement_traces=(
                    RequirementTrace(
                        requirement_id=FunctionalRequirementId("FR-1"),
                        scenario_ids=(ScenarioId("SCN-1"),),
                    ),
                ),
                depends_on=(),
            ),
        ),
    )


class TestTaskExecutionService:
    """Service boundary: takes plan + runner, returns TaskResult."""

    def test_execute_task_returns_task_result(self, tmp_path: Path, fake_plan: object) -> None:
        from seharness.execution.service import TaskExecutionService, TaskResult

        repo = tmp_path / "repo"
        repo.mkdir()

        service = TaskExecutionService(
            repo_root=repo,
            execution_root=tmp_path,
        )

        result = service.execute(
            plan=fake_plan,  # type: ignore[arg-type]
            task_id="T-1",
            runner=_stub_runner(
                tmp_path / "execution" / "T-1" / "red",
                tmp_path / "execution" / "T-1" / "green",
            ),
        )

        assert isinstance(result, TaskResult)
        assert result.task_id == "T-1"
        assert result.completed is True

    def test_execute_task_persists_task_result_json(
        self, tmp_path: Path, fake_plan: object
    ) -> None:
        from seharness.execution.service import TaskExecutionService

        repo = tmp_path / "repo"
        repo.mkdir()

        service = TaskExecutionService(
            repo_root=repo,
            execution_root=tmp_path,
        )

        service.execute(
            plan=fake_plan,  # type: ignore[arg-type]
            task_id="T-1",
            runner=_stub_runner(
                tmp_path / "execution" / "T-1" / "red",
                tmp_path / "execution" / "T-1" / "green",
            ),
        )

        result_path = tmp_path / "execution" / "T-1" / "task-result.json"
        assert result_path.exists()
        payload = json.loads(result_path.read_text())
        assert payload["task_id"] == "T-1"
        assert payload["completed"] is True

    def test_execute_task_rejects_when_evidence_missing(
        self, tmp_path: Path, fake_plan: object
    ) -> None:
        from seharness.execution.service import (
            TaskExecutionService,
            TaskEvidenceError,
        )

        repo = tmp_path / "repo"
        repo.mkdir()

        def noop_runner(r: Path, g: Path) -> None:
            # Writes nothing \u2014 validator should reject.
            return None

        service = TaskExecutionService(
            repo_root=repo,
            execution_root=tmp_path,
        )

        with pytest.raises(TaskEvidenceError):
            service.execute(
                plan=fake_plan,  # type: ignore[arg-type]
                task_id="T-1",
                runner=noop_runner,
            )

    def test_execute_task_unknown_task_id_rejected(
        self, tmp_path: Path, fake_plan: object
    ) -> None:
        from seharness.execution.service import TaskExecutionService, TaskNotFoundError

        repo = tmp_path / "repo"
        repo.mkdir()

        service = TaskExecutionService(
            repo_root=repo,
            execution_root=tmp_path,
        )

        with pytest.raises(TaskNotFoundError):
            service.execute(
                plan=fake_plan,  # type: ignore[arg-type]
                task_id="T-999",
                runner=_stub_runner(tmp_path / "r", tmp_path / "g"),
            )


class TestTaskResultShape:
    """TaskResult exposes the documented fields."""

    def test_task_result_has_documented_fields(self, tmp_path: Path) -> None:
        from seharness.execution.service import TaskResult

        r = TaskResult(
            task_id="T-1",
            completed=True,
            evidence_root=tmp_path,
            red_exit_code=1,
            green_exit_code=0,
            violations=(),
        )
        assert r.task_id == "T-1"
        assert r.completed is True
        assert r.red_exit_code == 1
        assert r.green_exit_code == 0
        assert tuple(r.violations) == ()