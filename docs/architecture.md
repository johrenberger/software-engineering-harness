# Architecture — Canonical Orchestrator

This document describes the canonical workflow engine that
``Orchestrator`` provides and explains how every entry point
(``/feature``, ``seharness run``, Telegram, dashboard, E2E test)
funnels through it.

## Goal

A single, well-tested workflow engine that turns a feature request
into a draft pull request by composing the existing services in the
canonical phase order.

## Phase sequence

The orchestrator runs the canonical 12-phase sequence declared in
SPEC §"Phase 8":

```
feature_request
    → repository_discovery
    → specification
    → planning
    → implementation
    → validation
    → remediation
    → review
    → draft_pr
    → ci
    → ready
    → completed
```

Every phase emits a ``PipelineEvent`` and the orchestrator records the
state transition in the controller's ``RunLedger``.

## Entry points

```
┌────────────────────┐
│   CLI (seharness)  │──┐
└────────────────────┘  │
                        ▼
┌────────────────────┐   ┌──────────────────────────┐   ┌──────────────────┐
│  Telegram /feature │──▶│ ControllerApplicationSvc │──▶│   Orchestrator   │
└────────────────────┘   └──────────────────────────┘   └──────────────────┘
┌────────────────────┐                                            │
│  Dashboard /feat  │────────────────────────────────────────────┤
└────────────────────┘                                            │
                        ┌──────────────────────────┐              │
                        │  VerticalSlicePipeline   │──────────────┘
                        │   (thin adapter over     │   (legacy)
                        │   orchestrator)          │
                        └──────────────────────────┘
                                                                  │
                                                                  ▼
                                                ┌──────────────────────────┐
                                                │  Existing services       │
                                                │  - RepositoryProfiler    │
                                                │  - TaskExecutionService  │
                                                │  - PullRequestClient     │
                                                │  - CiMonitor             │
                                                │  - RunLedger             │
                                                └──────────────────────────┘
```

Every external entry point — CLI subcommand, Telegram ``/feature``,
dashboard widget, E2E test — invokes either the
``Orchestrator.start_run(...)`` method directly, or the
``ControllerApplicationService.feature_request(...)`` method which
delegates to the orchestrator when an ``Orchestrator`` instance is
wired in.

## Terminal states

The orchestrator returns one of four terminal states for every run:

| Terminal state | Meaning | RunState enum |
|---|---|---|
| ``"completed"`` | All 12 phases succeeded | ``RunState.COMPLETE`` |
| ``"failed"``    | An unrecoverable error in a phase | ``RunState.FAILED`` |
| ``"blocked"``   | Policy violation; needs intervention | ``RunState.BLOCKED`` |
| ``"paused"``    | Awaiting resume / approval | ``RunState.PAUSED`` |

The orchestrator emits ``"completed"`` to match the SPEC §"Phase 8"
phrase (SPEC line 587); the controller's ``RunState.COMPLETE.value``
internally stores ``"complete"`` (the legacy in-memory enum). The
boundary translation lives in
``Orchestrator.start_run`` (see
``src/seharness/orchestrator/orchestrator.py``).

## Auto-merge prevention

The orchestrator adds a **6th layer** to the auto-merge prevention
contract (the previous slices shipped layers 1–5):

> Layer 6 (Cluster A): ``Orchestrator`` exposes no ``merge*`` /
> ``auto_merge*`` / ``gh_merge`` methods. The contract is enforced
> by ``tests/unit/orchestrator/test_orchestrator_mutation_killers.py::test_orchestrator_has_no_merge_method``.

## Configuration

``OrchestratorConfig`` (frozen dataclass) controls:

| Field | Default | Purpose |
|---|---|---|
| ``execution_root`` | ``".openclaw-runs/orchestrator"`` | Where artifacts are written |
| ``auto_remediate`` | ``True`` | Whether to attempt remediation on validation failure |
| ``max_remediation_attempts`` | ``3`` | Budget for remediation loop |
| ``max_validation_attempts`` | ``3`` | Budget for validation loop |
| ``pr_draft`` | ``True`` | PRs are created as drafts (never auto-merged) |
| ``use_real_subprocess`` | ``False`` | If True, validation runs real subprocesses |

## Artifacts produced per run

The orchestrator writes the following files under
``<execution_root>/<run_id>/``:

```
<repo_path>/                             ← target repo (untouched after revert)
<repo_profile.json>                      ← repository profile
specification.json                       ← derived from feature description
plan.json                                ← Plan with one Task
execution/<task_id>/red/{command,stdout,stderr,result}.json   ← RED evidence
execution/<task_id>/green/{command,stdout,stderr,result}.json ← GREEN evidence
execution/<task_id>/task-result.json     ← TaskResult
review-verdict.json                      ← reviewer verdict
```

The draft PR is issued via ``PullRequestClient``; the CI readiness
check uses ``CiMonitor``; the run state is recorded in ``RunLedger``.

## Deferred capabilities

| Concern | Owner |
|---|---|
| Sandbox / isolated execution | `src/seharness/sandbox/` |
| Real production adapters (TaskExecutor, CiMonitor, RunLedger) | `src/seharness/controller/real_adapters.py` |
| Deterministic replay on resume | `Orchestrator.start_run(resume_from_run_id=...)` |
| Real model adapters (Codex, MiniMax) | `src/seharness/models/` |
| Durable ledger on disk | `FileRunLedger` (in-memory only today) |
| Concurrent-run safety | `LeaseStore` (shipped v0.2.0) |

## See also

- ``src/seharness/orchestrator/orchestrator.py`` — the orchestrator
- ``src/seharness/pipeline/vertical_slice.py`` — the adapter
- ``src/seharness/controller/application_service.py`` — the controller
- ``src/seharness/cli.py`` — the CLI entry point
- ``tests/unit/orchestrator/`` — the orchestrator tests