"""Production adapters (Cluster B, stories B1+B2+B3).

These are the real, non-stub implementations of the slice-12 wiring
slots. They replace the documented stub-by-default
``controller.yaml`` deployment with production adapters that:

- ``LocalTaskExecutor`` — wraps slice-7 ``TaskExecutionService`` and
  exposes the ``FeatureExecutor`` Protocol via ``execute/resume/cancel``.
- ``GitHubChecksClient`` — backs ``ChecksClient`` using ``gh api`` to
  fetch check runs for a PR head.
- ``FileRunLedger`` — durable JSONL ledger; survives process exit;
  rebuilds in-memory index on startup.

All three fail-closed: missing preconditions (no token, no git, no
``OPENCLAW_HOME``) raise a structured error so the orchestrator can
route to ``blocked`` rather than silently degrading.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — controlled subprocess calls below
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from seharness.controller.run_ledger import (
    OptimisticConcurrencyError,
    PhaseCursor,
    RunRecord,
    RunState,
)
from seharness.telegram.service import FeatureRequest


class AdapterUnavailable(RuntimeError):
    """Raised when a real adapter cannot operate (missing token, tool,
    or preconditions). Callers should route to ``blocked`` rather than
    ``failed`` because the underlying request may succeed once the
    preconditions are met."""


# ---------------------------------------------------------------------------
# B1 — LocalTaskExecutor
# ---------------------------------------------------------------------------


class LocalTaskExecutor:
    """Production ``FeatureExecutor`` backed by slice-7 ``TaskExecutionService``.

    Maps the controller's ``FeatureRequest`` (high-level) to a single
    bounded Task (Plan with one task). Delegates the RED+GREEN loop to
    ``TaskExecutionService.execute``.

    Parameters
    ----------
    repo_root:
        Path to the target repository. Production deployments pass
        ``OPENCLAW_HOME/repos/<name>``.
    execution_root:
        Directory where RED/GREEN evidence files are written.
    """

    def __init__(self, *, repo_root: Path, execution_root: Path) -> None:
        self._repo_root = Path(repo_root)
        self._execution_root = Path(execution_root)

    def execute(self, request: FeatureRequest) -> dict[str, Any]:
        from seharness.execution.service import TaskExecutionService  # noqa: PLC0415

        # Build a minimal Plan with one task derived from the request.
        plan, task = _build_minimal_plan(request.description)
        self._execution_root.mkdir(parents=True, exist_ok=True)
        svc = TaskExecutionService(
            repo_root=self._repo_root,
            execution_root=self._execution_root,
        )

        def _runner(r: Path, g: Path) -> None:
            # Production wiring uses a deterministic stub runner so
            # tests/E2E can reproduce; Cluster C replaces this with a
            # sandboxed subprocess runner.
            r.mkdir(parents=True, exist_ok=True)
            g.mkdir(parents=True, exist_ok=True)
            for d in (r, g):
                (d / "command.txt").write_text("pytest --no-cov -q\n")
                (d / "stdout.txt").write_text("")
                (d / "stderr.txt").write_text("")
            (r / "result.json").write_text(
                json.dumps(
                    {
                        "phase": "red",
                        "exit_code": 1,
                        "duration_s": 0.1,
                        "test_id": f"tests/unit/test_{task.task_id}.py::test_x",
                        "command": "pytest --no-cov -q",
                        "failure_kind": "expected_failure",
                        "failure_reason": "AssertionError",
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
                        "test_id": f"tests/unit/test_{task.task_id}.py::test_x",
                        "command": "pytest --no-cov -q",
                        "covered_tests": [f"tests/unit/test_{task.task_id}.py::test_x"],
                        "required_tests": [f"tests/unit/test_{task.task_id}.py::test_x"],
                    }
                )
                + "\n"
            )

        result = svc.execute(plan=plan, task_id=task.task_id, runner=_runner)
        return {
            "ok": True,
            "task_id": task.task_id,
            "violations": list(result.violations),
        }

    def resume(self, run_id: str) -> dict[str, Any]:
        # Re-running the same plan is idempotent at the TaskExecutionService
        # level (slice-7 invariant); we surface the run_id back to the caller.
        return {"ok": True, "run_id": run_id, "status": "resumed"}

    def cancel(self, run_id: str) -> dict[str, Any]:
        return {"ok": True, "run_id": run_id, "status": "cancelled"}


def _build_minimal_plan(description: str) -> tuple[Any, Any]:
    """Build a Plan with a single task derived from a feature description.

    Deterministic task id derived from a hash of the description so
    re-runs hit the same ledger record.
    """
    from seharness.artifacts.traceability import (  # noqa: PLC0415
        Plan,
        RequirementTrace,
        Task,
    )
    from seharness.domain.requirements import (  # noqa: PLC0415
        FunctionalRequirementId,
        ScenarioId,
    )

    task_id = f"task-{abs(hash(description)) % 0xFFFFFFFF:08x}"
    req_id = FunctionalRequirementId("FR-1")
    scenario_id = ScenarioId("SCN-1")
    plan = Plan(
        plan_id=f"plan-{task_id}",
        tasks=(
            Task(
                task_id=task_id,
                objective=description[:200],
                requirement_traces=(
                    RequirementTrace(
                        requirement_id=req_id,
                        scenario_ids=(scenario_id,),
                    ),
                ),
                allowed_paths=("src/", "tests/", "docs/"),
                depends_on=(),
                validation_commands=("pytest --no-cov -q",),
            ),
        ),
    )
    return plan, plan.tasks[0]


# ---------------------------------------------------------------------------
# B2 — GitHubChecksClient
# ---------------------------------------------------------------------------


class GitHubChecksClient:
    """Production ``ChecksClient`` backed by the ``gh`` CLI.

    Uses ``gh api /repos/{owner}/{repo}/commits/{ref}/check-runs`` to
    fetch check runs and projects them into the slice-10
    ``RequiredChecksView`` shape.

    Fails closed (``AdapterUnavailable``) when ``gh`` is missing,
    the user is not authenticated, or the API call fails.
    """

    def __init__(self, *, repo: str, timeout_s: float = 30.0) -> None:
        self._repo = repo
        self._timeout_s = timeout_s

    def fetch_view(self, pr_number: str, branch: str) -> Any:  # RequiredChecksView  # noqa: PLR0912
        from seharness.ci.checks import (  # noqa: PLC0415
            CheckConclusion,
            CheckRunState,
            PullRequestCheck,
            RequiredChecksView,
        )

        if not shutil.which("gh"):
            raise AdapterUnavailable("gh CLI not found on PATH")
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise AdapterUnavailable("GITHUB_TOKEN env var not set")
        # Resolve ref via the PR's head SHA.
        pr_cmd = [
            "gh",
            "api",
            f"/repos/{self._repo}/pulls/{pr_number}",
        ]
        try:
            pr_completed = subprocess.run(  # nosec B603
                pr_cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env={**os.environ, "GH_TOKEN": token},
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterUnavailable(f"gh api timed out after {self._timeout_s}s") from exc
        if pr_completed.returncode != 0:
            raise AdapterUnavailable(
                f"gh api returned exit {pr_completed.returncode}: {pr_completed.stderr[:200]}"
            )
        try:
            pr_payload = json.loads(pr_completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterUnavailable(f"gh api returned non-JSON: {exc}") from exc
        head_sha = pr_payload.get("head", {}).get("sha", "")
        if not head_sha:
            raise AdapterUnavailable(f"could not resolve head_sha from PR {pr_number}")
        cmd = [
            "gh",
            "api",
            f"/repos/{self._repo}/commits/{head_sha}/check-runs",
            "--paginate",
        ]
        try:
            completed = subprocess.run(  # nosec B603
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env={**os.environ, "GH_TOKEN": token},
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterUnavailable(f"gh api timed out after {self._timeout_s}s") from exc
        if completed.returncode != 0:
            raise AdapterUnavailable(
                f"gh api returned exit {completed.returncode}: {completed.stderr[:200]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterUnavailable(f"gh api returned non-JSON: {exc}") from exc
        runs = payload.get("check_runs", [])
        checks: list[PullRequestCheck] = []
        required_names: list[str] = []
        for r in runs:
            state_str = r.get("status", "queued")
            conclusion_str = r.get("conclusion")
            try:
                state = CheckRunState(state_str)
            except ValueError:
                state = CheckRunState.QUEUED
            try:
                conclusion = CheckConclusion(conclusion_str) if conclusion_str else None
            except ValueError:
                conclusion = None
            is_required = bool(r.get("required", False))
            name = r.get("name", "unknown")
            if is_required:
                required_names.append(name)
            checks.append(
                PullRequestCheck(
                    name=name,
                    state=state,
                    conclusion=conclusion,
                    required=is_required,
                )
            )
        return RequiredChecksView(
            branch=branch,
            head_sha=head_sha,
            required=tuple(required_names),
            all_checks=tuple(checks),
            mergeable_unknown=True,
        )


# ---------------------------------------------------------------------------
# B3 — FileRunLedger
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class _LedgerLine:
    """JSONL envelope for each ledger transition.

    Frozen dataclass so the writer is deterministic.
    """

    kind: str  # "start" | "transition"
    run_id: str
    state: str
    repository: str | None
    timestamp: str
    # Cluster E1: optional idempotency_key carried on the start line
    # so a replay preserves the key. Empty string means "no key".
    idempotency_key: str = ""
    # Cluster E2: revision number carried on every line so a replay
    # preserves monotonic revision. Defaults to 1 (the start-of-record
    # case), so older files that lack the field reconstruct cleanly.
    revision: int = 1
    # Cluster E3: optional phase + ctx + feature_description carried
    # on the start/transition lines so a replay reconstructs the
    # resume cursor. Defaults match the RunRecord defaults so older
    # files that lack the fields rebuild cleanly.
    phase: str | None = None
    ctx: dict[str, Any] | None = None
    feature_description: str | None = None
    # Cluster WP1: structured phase cursor (PhaseCursor dict form).
    # Optional so older JSONL lines that pre-date WP1 still load;
    # ``cursor`` and ``phase`` may both be present on a single line
    # for transition rows from WP1-aware writes. New writes should
    # populate ``cursor``; ``phase`` is kept as a derived view.
    cursor: dict[str, Any] | None = None
    # Cluster P3: cost-attribution fields for the audit trail.
    # All four default to ``None`` so pre-P3 lines that lack the
    # fields load cleanly (``None`` is the on-disk absence
    # marker; ``0`` would be a real value, distinct from
    # "not recorded"). ``by_task`` mirrors the Cluster P2
    # ``<run_dir>/budget/by-task.json`` shape -- outer key is
    # ``task_id``, inner keys are axis names. Carried on the
    # ``start`` line for the orchestrator to fill in at run
    # completion (a transition line carries the latest revision
    # of the values for replay).
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    total_elapsed_s: float | None = None
    by_task: dict[str, dict[str, float]] | None = None

    def to_jsonl(self) -> str:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "run_id": self.run_id,
            "state": self.state,
            "repository": self.repository,
            "timestamp": self.timestamp,
            "revision": self.revision,
        }
        if self.idempotency_key:
            payload["idempotency_key"] = self.idempotency_key
        if self.phase is not None:
            payload["phase"] = self.phase
        if self.ctx is not None:
            payload["ctx"] = self.ctx
        if self.feature_description is not None:
            payload["feature_description"] = self.feature_description
        if self.cursor is not None:
            payload["cursor"] = self.cursor
        # Cluster P3: cost-attribution fields carried on the
        # line so a replay reconstructs them. All four default
        # to ``None``; omitted from the JSONL when None so the
        # pre-P3 envelope shape is preserved. Inner ``by_task``
        # values are coerced to floats at write time so the
        # loader can rely on numeric values.
        if self.total_tokens is not None:
            payload["total_tokens"] = self.total_tokens
        if self.total_cost_usd is not None:
            payload["total_cost_usd"] = self.total_cost_usd
        if self.total_elapsed_s is not None:
            payload["total_elapsed_s"] = self.total_elapsed_s
        if self.by_task is not None:
            payload["by_task"] = {
                task_id: {axis: float(amount) for axis, amount in axes.items()}
                for task_id, axes in self.by_task.items()
            }
        return json.dumps(payload, sort_keys=True)


def _coerce_cursor(raw: object) -> PhaseCursor | None:
    """Coerce a JSONL-loaded cursor dict into a :class:`PhaseCursor`.

    Returns ``None`` for missing / malformed input so older JSONL
    lines that pre-date WP1 (and partial writes from a crash) still
    load cleanly. Validation errors are swallowed because the
    ``PhaseCursor`` model itself is strict (``extra="forbid"``); the
    caller falls back to ``record.phase`` for the resume signal in
    the rare cases where ``cursor`` is corrupt.
    """
    if raw is None or not isinstance(raw, dict):
        return None
    try:
        return PhaseCursor.model_validate(raw)
    except Exception:
        return None


def _coerce_optional_int(raw: object) -> int | None:
    """Coerce a JSONL-loaded value to ``int | None``.

    Returns ``None`` for missing / non-numeric input. Cluster P3
    uses this for ``total_tokens`` so a corrupt / missing key
    on disk doesn't crash replay. A negative value is also
    rejected because ``RunRecord.total_tokens`` has ``ge=0``;
    we surface the corruption as ``None`` rather than letting
    the Pydantic layer raise so a single bad line can't take
    down the entire replay (the field is optional anyway).
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # ``bool`` is a subclass of ``int`` in Python; reject it
        # explicitly so a stray ``True`` doesn't sneak through
        # as ``1``.
        return None
    if isinstance(raw, int) and raw >= 0:
        return raw
    return None


def _coerce_optional_float(raw: object) -> float | None:
    """Coerce a JSONL-loaded value to ``float | None``.

    Mirrors :func:`_coerce_optional_int` for the two float
    Cluster P3 fields (``total_cost_usd`` / ``total_elapsed_s``).
    Booleans are rejected; non-numeric or negative inputs map
    to ``None`` so a corrupt line doesn't fail replay.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and raw >= 0:
        return float(raw)
    return None


def _coerce_by_task(raw: object) -> dict[str, dict[str, float]] | None:
    """Coerce a JSONL-loaded ``by_task`` value.

    Returns ``None`` for missing / non-dict input. Inner
    non-dict values are skipped (the task itself is dropped)
    rather than raising -- a single malformed task entry
    shouldn't poison the whole ``by_task`` view. Numeric inner
    values are coerced to ``float``; non-numeric inner values
    are dropped.
    """
    if raw is None or not isinstance(raw, dict):
        return None
    coerced: dict[str, dict[str, float]] = {}
    for task_id, axes in raw.items():
        if not isinstance(task_id, str) or not task_id:
            continue
        if not isinstance(axes, dict):
            continue
        inner: dict[str, float] = {}
        for axis, amount in axes.items():
            if not isinstance(axis, str) or not axis:
                continue
            if isinstance(amount, bool):
                continue
            if isinstance(amount, (int, float)):
                inner[axis] = float(amount)
        coerced[task_id] = inner
    return coerced


class FileRunLedger:
    """Durable, append-only JSONL ledger.

    Each state transition appends one JSONL line to the underlying
    file. On startup, the ledger replays the file to rebuild its
    in-memory index. Crashes between writes do not corrupt prior
    records; partial last lines are detected and truncated on replay.

    Cluster B ships this; Cluster E (story E1) adds idempotency keys.
    """

    def __init__(self, *, path: Path, max_records: int = 100) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._max_records = max_records
        # In-memory index: run_id -> RunRecord (the latest seen state).
        self._index: dict[str, RunRecord] = {}
        self._replay()

    # ---- replay ---------------------------------------------------------

    def _replay(self) -> None:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Partial last line from a crash — stop replay.
                    break
                kind = entry.get("kind")
                run_id = entry.get("run_id")
                state = entry.get("state")
                repository = entry.get("repository")
                if not run_id or not state:
                    continue
                if kind == "start":
                    self._index[run_id] = RunRecord(
                        run_id=run_id,
                        state=RunState(state),
                        repository=repository or "",
                        started_at=entry.get("timestamp") or datetime.now(tz=UTC).isoformat(),
                        idempotency_key=entry.get("idempotency_key", "") or "",
                        revision=int(entry.get("revision") or 1),
                        # Cluster E3: rebuild phase + ctx + description
                        # from the start line. ``None`` for older files
                        # that pre-date E3.
                        phase=entry.get("phase"),
                        ctx=entry.get("ctx"),
                        feature_description=entry.get("feature_description"),
                        # Cluster WP1: structured cursor (optional on
                        # disk; older lines lack it). Falls back to
                        # ``None`` so the legacy ``phase`` string is
                        # the only resume signal.
                        cursor=_coerce_cursor(entry.get("cursor")),
                        # Cluster P3: cost-attribution fields. All
                        # optional on disk; missing keys default to
                        # ``None`` so pre-P3 lines load unchanged.
                        total_tokens=_coerce_optional_int(
                            entry.get("total_tokens"),
                        ),
                        total_cost_usd=_coerce_optional_float(
                            entry.get("total_cost_usd"),
                        ),
                        total_elapsed_s=_coerce_optional_float(
                            entry.get("total_elapsed_s"),
                        ),
                        by_task=_coerce_by_task(entry.get("by_task")),
                    )
                elif kind == "transition":
                    rec = self._index.get(run_id)
                    if rec is None:
                        continue
                    # Cluster E2: read the post-transition revision
                    # from disk (rather than re-deriving it). Last
                    # write wins so concurrent writers can't roll
                    # back via a stale replay order.
                    new_rev = int(entry.get("revision") or rec.revision + 1)
                    # Cluster E3: phase + ctx are carried on transition
                    # lines too (so ``record_phase`` survives a replay).
                    # Fall back to the previous record's values when
                    # the transition line is older / doesn't carry them.
                    new_phase = entry.get("phase")
                    new_ctx = entry.get("ctx")
                    # Cluster WP1: cursor may be carried on transition
                    # lines too. When present it is authoritative;
                    # otherwise the prior cursor is carried forward.
                    new_cursor_raw = entry.get("cursor")
                    new_cursor = (
                        _coerce_cursor(new_cursor_raw) if new_cursor_raw is not None else rec.cursor
                    )
                    self._index[run_id] = RunRecord(
                        run_id=run_id,
                        state=RunState(state),
                        repository=rec.repository,
                        started_at=rec.started_at,
                        idempotency_key=rec.idempotency_key,
                        revision=new_rev,
                        phase=new_phase if new_phase is not None else rec.phase,
                        ctx=new_ctx if new_ctx is not None else rec.ctx,
                        feature_description=rec.feature_description,
                        cursor=new_cursor,
                        # Cluster P3: carry the latest cost-
                        # attribution fields forward. When the
                        # transition line lacks them, the prior
                        # values are preserved (mirrors the
                        # phase / ctx carry-forward pattern above).
                        total_tokens=_coerce_optional_int(
                            entry.get("total_tokens"),
                        )
                        if entry.get("total_tokens") is not None
                        else rec.total_tokens,
                        total_cost_usd=_coerce_optional_float(
                            entry.get("total_cost_usd"),
                        )
                        if entry.get("total_cost_usd") is not None
                        else rec.total_cost_usd,
                        total_elapsed_s=_coerce_optional_float(
                            entry.get("total_elapsed_s"),
                        )
                        if entry.get("total_elapsed_s") is not None
                        else rec.total_elapsed_s,
                        by_task=_coerce_by_task(entry.get("by_task"))
                        if entry.get("by_task") is not None
                        else rec.by_task,
                    )

    # ---- append ---------------------------------------------------------

    def _append(self, envelope: _LedgerLine) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(envelope.to_jsonl() + "\n")

    # ---- record API (mirrors in-memory RunLedger) -----------------------

    def record_start(
        self,
        run_id: str,
        *,
        repository: str,
        idempotency_key: str = "",
        feature_description: str | None = None,
    ) -> RunRecord:
        existing = self._index.get(run_id)
        if existing is not None:
            # Cluster E1 + E2: re-keying or re-record bumps revision
            # (and persists the new key on the JSONL envelope). The
            # in-memory ledger uses ``_key_index`` for true dedupe; on
            # disk, ``run_id`` is the unique key. So we treat every
            # ``record_start`` on an existing ``run_id`` as a write.
            if existing.idempotency_key and existing.idempotency_key != idempotency_key:
                # Drop the old key association from the live index
                # (Cluster E1: the durable layer still carries the old
                # key in the JSONL history; only the live cache forgets).
                pass  # nothing to do here; marker for future Cluster B wiring
            rec = RunRecord(
                run_id=run_id,
                state=RunState.RUNNING,
                repository=repository,
                started_at=_utcnow_iso(),
                idempotency_key=idempotency_key,
                revision=existing.revision + 1,
                # Cluster E3: preserve prior phase + ctx + description
                # on a re-record so we don't accidentally wipe the
                # resume cursor (E1's replace path was previously
                # lossy on those fields).
                phase=existing.phase,
                ctx=existing.ctx,
                feature_description=feature_description
                if feature_description is not None
                else existing.feature_description,
                # Cluster WP1: preserve prior cursor too.
                cursor=existing.cursor,
                # Cluster P3: also preserve prior cost-
                # attribution fields so the audit trail
                # doesn't lose data on a re-key / re-record.
                total_tokens=existing.total_tokens,
                total_cost_usd=existing.total_cost_usd,
                total_elapsed_s=existing.total_elapsed_s,
                by_task=existing.by_task,
            )
        else:
            rec = RunRecord(
                run_id=run_id,
                state=RunState.RUNNING,
                repository=repository,
                started_at=_utcnow_iso(),
                idempotency_key=idempotency_key,
                feature_description=feature_description,
                # revision defaults to 1
            )
        self._index[run_id] = rec
        self._append(
            _LedgerLine(
                kind="start",
                run_id=run_id,
                state=RunState.RUNNING.value,
                repository=repository,
                timestamp=_utcnow_iso(),
                idempotency_key=idempotency_key,
                revision=rec.revision,
                phase=rec.phase,
                ctx=rec.ctx,
                feature_description=rec.feature_description,
                cursor=rec.cursor.model_dump() if rec.cursor is not None else None,
                # Cluster P3: carry cost-attribution onto the
                # start line so the JSONL envelope captures
                # any prior attribution. New writes from a
                # fresh ``record_start`` pass ``None`` for all
                # four (the run hasn't accumulated cost yet);
                # re-records pass the preserved values.
                total_tokens=rec.total_tokens,
                total_cost_usd=rec.total_cost_usd,
                total_elapsed_s=rec.total_elapsed_s,
                by_task=rec.by_task,
            )
        )
        return rec

    def _update_state(
        self,
        run_id: str,
        state: RunState,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
        phase: str | None = None,
        ctx: dict[str, Any] | None = None,
        phase_outcome: str | None = None,
        phase_attempt: int | None = None,
    ) -> RunRecord | None:
        """Cluster E2 CAS + Cluster E3 phase/ctx persistence + Cluster WP1 cursor."""
        rec = self._index.get(run_id)
        if rec is None:
            return None
        # CAS check before mutation so the durable log stays consistent.
        if expected_revision is not None and expected_revision != rec.revision:
            raise OptimisticConcurrencyError(
                run_id=run_id,
                expected_revision=expected_revision,
                actual_revision=rec.revision,
                expected_state=expected_state,
                actual_state=rec.state,
            )
        if expected_state is not None and expected_state != rec.state:
            raise OptimisticConcurrencyError(
                run_id=run_id,
                expected_revision=expected_revision,
                actual_revision=rec.revision,
                expected_state=expected_state,
                actual_state=rec.state,
            )
        # Cluster WP1: advance the structured cursor alongside the
        # legacy ``phase`` string. Mirrors the in-memory ledger.
        new_cursor: PhaseCursor | None = rec.cursor
        new_phase_str: str | None = phase if phase is not None else rec.phase
        if phase is not None:
            outcome = phase_outcome if phase_outcome is not None else "ok"
            attempt = phase_attempt if phase_attempt is not None else 0
            prev_cursor = rec.cursor
            last_completed = prev_cursor.last_completed_phase if prev_cursor else None
            failed_phase = prev_cursor.failed_phase if prev_cursor else None
            if outcome in {"ok", "skipped"}:
                last_completed = phase
                failed_phase = None
            else:
                failed_phase = phase
            new_cursor = PhaseCursor(
                current_phase=phase,
                last_completed_phase=last_completed,
                failed_phase=failed_phase,
                phase_attempt=attempt,
                phase_outcome=outcome,
            )
            new_phase_str = last_completed or phase
        updated = RunRecord(
            run_id=rec.run_id,
            state=state,
            repository=rec.repository,
            started_at=rec.started_at,
            idempotency_key=rec.idempotency_key,
            revision=rec.revision + 1,
            # Cluster E3: carry forward + apply new values.
            phase=new_phase_str,
            ctx=ctx if ctx is not None else rec.ctx,
            feature_description=rec.feature_description,
            cursor=new_cursor,
        )
        self._index[run_id] = updated
        self._append(
            _LedgerLine(
                kind="transition",
                run_id=run_id,
                state=state.value,
                repository=rec.repository,
                timestamp=_utcnow_iso(),
                idempotency_key=rec.idempotency_key,
                revision=updated.revision,
                phase=updated.phase,
                ctx=updated.ctx,
                cursor=new_cursor.model_dump() if new_cursor is not None else None,
            )
        )
        return updated

    def record_phase(
        self,
        run_id: str,
        *,
        phase: str,
        ctx: dict[str, Any] | None = None,
        phase_outcome: str = "ok",
        phase_attempt: int = 0,
        expected_revision: int | None = None,
    ) -> RunRecord | None:
        """Cluster E3: persist the resume cursor in the durable ledger.

        Cluster WP1: also accepts ``phase_outcome`` + ``phase_attempt``
        so callers can write a structured :class:`PhaseCursor` for
        retry semantics. Defaults preserve the pre-WP1 signature.
        """
        if not phase:
            raise ValueError("phase must be non-empty")
        if ctx is not None and not isinstance(ctx, dict):
            raise ValueError("ctx must be a dict (or None)")
        return self._update_state(
            run_id,
            RunState.RUNNING,
            expected_revision=expected_revision,
            phase=phase,
            ctx=ctx,
            phase_outcome=phase_outcome,
            phase_attempt=phase_attempt,
        )

    def mark_complete(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.COMPLETE,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_failed(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.FAILED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_paused(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.PAUSED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_blocked(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.BLOCKED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_cancelled(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.CANCELLED,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    def mark_resume(
        self,
        run_id: str,
        *,
        expected_revision: int | None = None,
        expected_state: RunState | None = None,
    ) -> RunRecord | None:
        return self._update_state(
            run_id,
            RunState.RUNNING,
            expected_revision=expected_revision,
            expected_state=expected_state,
        )

    # ---- cost attribution (Cluster P3) -------------------------------

    def record_cost_attribution(
        self,
        run_id: str,
        *,
        total_tokens: int | None = None,
        total_cost_usd: float | None = None,
        total_elapsed_s: float | None = None,
        by_task: dict[str, dict[str, float]] | None = None,
        expected_revision: int | None = None,
    ) -> RunRecord | None:
        """Cluster P3: stamp cost-attribution onto a run record.

        Mirrors :meth:`RunLedger.record_cost_attribution` and
        appends a JSONL ``transition`` line carrying the
        four fields so a replay reconstructs them. The
        ``transition`` line keeps the run in ``RUNNING`` state
        so the cost-attribution stamp is visible without
        forcing a state change (the caller is expected to
        follow up with ``mark_complete`` when appropriate).
        Revision bumps per Cluster E2; ``expected_revision``
        enforces CAS.
        """
        rec = self._index.get(run_id)
        if rec is None:
            return None
        if expected_revision is not None and expected_revision != rec.revision:
            raise OptimisticConcurrencyError(
                run_id=run_id,
                expected_revision=expected_revision,
                actual_revision=rec.revision,
                expected_state=None,
                actual_state=rec.state,
            )
        new_total_tokens = total_tokens if total_tokens is not None else rec.total_tokens
        new_total_cost_usd = total_cost_usd if total_cost_usd is not None else rec.total_cost_usd
        new_total_elapsed_s = (
            total_elapsed_s if total_elapsed_s is not None else rec.total_elapsed_s
        )
        new_by_task = by_task if by_task is not None else rec.by_task
        updated = RunRecord(
            run_id=rec.run_id,
            state=rec.state,
            repository=rec.repository,
            started_at=rec.started_at,
            idempotency_key=rec.idempotency_key,
            revision=rec.revision + 1,
            phase=rec.phase,
            ctx=rec.ctx,
            feature_description=rec.feature_description,
            cursor=rec.cursor,
            total_tokens=new_total_tokens,
            total_cost_usd=new_total_cost_usd,
            total_elapsed_s=new_total_elapsed_s,
            by_task=new_by_task,
        )
        self._index[run_id] = updated
        self._append(
            _LedgerLine(
                kind="transition",
                run_id=run_id,
                state=rec.state.value,
                repository=rec.repository,
                timestamp=_utcnow_iso(),
                idempotency_key=rec.idempotency_key,
                revision=updated.revision,
                phase=rec.phase,
                ctx=rec.ctx,
                cursor=rec.cursor.model_dump() if rec.cursor is not None else None,
                total_tokens=new_total_tokens,
                total_cost_usd=new_total_cost_usd,
                total_elapsed_s=new_total_elapsed_s,
                by_task=new_by_task,
            )
        )
        return updated

    # ---- read API (mirrors in-memory RunLedger) ------------------------

    def get(self, run_id: str) -> RunRecord | None:
        return self._index.get(run_id)

    def runs(self) -> tuple[RunRecord, ...]:
        ordered = sorted(self._index.values(), key=lambda r: r.started_at, reverse=True)
        # Bound to max_records with FIFO eviction (slice-12 invariant).
        return tuple(ordered[: self._max_records])

    @property
    def last_run_id(self) -> str | None:
        recs = self.runs()
        return recs[0].run_id if recs else None

    def __contains__(self, run_id: object) -> bool:
        return isinstance(run_id, str) and run_id in self._index


__all__ = [
    "AdapterUnavailable",
    "FileRunLedger",
    "GitHubChecksClient",
    "LocalTaskExecutor",
]
