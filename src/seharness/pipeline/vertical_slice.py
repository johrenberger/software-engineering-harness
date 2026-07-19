"""Vertical slice pipeline.

SPEC §"Phase 8" requires the pipeline to walk:

    feature_request → repository_discovery → specification → planning →
    implementation → validation → remediation → review → draft_pr →
    ci → ready → completed

The pipeline emits one ``PipelineEvent`` per phase and produces a
``PipelineResult`` whose ``terminal_state`` is one of the slice-2
terminal states.

**Cluster A (story A2):** ``VerticalSlicePipeline`` is now a thin
adapter over the canonical ``Orchestrator``. The simulated phase-name
loop that shipped in slice 13 has been removed; the orchestrator
composes the real slice-3..slice-10 services in the SPEC sequence.
The ``PipelineEvent`` / ``PipelineResult`` shapes are preserved so
existing callers (and the E2E test) continue to work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from seharness.controller.run_ledger import RunLedger
from seharness.orchestrator import Orchestrator
from seharness.orchestrator.orchestrator import (
    PipelineEvent as _OrchestratorPipelineEvent,
)
from seharness.orchestrator.orchestrator import (
    PipelineResult as _OrchestratorPipelineResult,
)


@dataclass(frozen=True)
class PipelineEvent:
    """A single phase event emitted by the pipeline.

    Same shape as the slice-13 ``PipelineEvent`` for backward compat.
    """

    phase: str
    timestamp: float
    detail: str = ""


@dataclass(frozen=True)
class PipelineResult:
    """Final result of a vertical-slice run.

    ``terminal_state`` is the SPEC phrase (``"complete"``, ``"failed"``,
    ``"blocked"``, ``"paused"``) — sourced from the controller's
    ``RunState`` enum so the contract is consistent across the harness.
    """

    run_id: str
    terminal_state: str
    events: tuple[PipelineEvent, ...] = ()


def _adapt_event(ev: _OrchestratorPipelineEvent) -> PipelineEvent:
    return PipelineEvent(phase=ev.phase, timestamp=ev.timestamp, detail=ev.detail)


def _adapt_result(res: _OrchestratorPipelineResult) -> PipelineResult:
    return PipelineResult(
        run_id=res.run_id,
        terminal_state=res.terminal_state,
        events=tuple(_adapt_event(e) for e in res.events),
    )


class VerticalSlicePipeline:
    """Adapter that runs the canonical orchestrator and exposes the
    slice-13 vertical-slice contract.

    Parameters
    ----------
    repo_path:
        Path to the target repository. The orchestrator writes
        artifacts under ``<execution_root>/<run_id>/``.
    feature_description:
        The feature request the orchestrator runs against. Defaults
        to a deterministic placeholder so the E2E test (which passes
        only ``repo_path``) continues to work.
    run_ledger:
        Optional shared ledger; defaults to a fresh in-memory ledger
        so the E2E test remains self-contained.
    orchestrator:
        Optional pre-built orchestrator. When ``None`` (default), the
        pipeline builds one with the default ``OrchestratorConfig``
        (stub runners, in-memory PR client) so tests never spawn
        subprocesses or hit the network.
    """

    def __init__(
        self,
        repo_path: Path,
        *,
        feature_description: str | None = None,
        run_ledger: RunLedger | None = None,
        orchestrator: Orchestrator | None = None,
    ) -> None:
        self._repo_path = Path(repo_path)
        self._feature_description = (
            feature_description
            if feature_description is not None
            else f"vertical-slice fixture run {uuid.uuid4().hex[:8]}"
        )
        self._run_ledger = run_ledger or RunLedger()
        self._orchestrator = orchestrator or Orchestrator(run_ledger=self._run_ledger)

    def run(self) -> PipelineResult:
        """Execute the full vertical-slice pipeline via the orchestrator."""
        result = self._orchestrator.start_run(
            feature_description=self._feature_description,
            repo_path=str(self._repo_path),
        )
        return _adapt_result(result)

    def transition(self, run_id: str, *, target: str) -> None:
        """Attempt to transition a terminal result (slice-2 invariant).

        ``complete`` is terminal and cannot transition.
        """
        rec = self._run_ledger.get(run_id)
        if rec is None:
            raise ValueError(f"unknown run_id: {run_id}")
        if rec.state.value == "complete":
            raise ValueError(
                f"cannot transition run {run_id} out of complete "
                f"(terminal state, slice-2 invariant)"
            )
        # No-op for non-complete (out of scope for Cluster A).
