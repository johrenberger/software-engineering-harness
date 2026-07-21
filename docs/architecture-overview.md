# Architecture Overview — Service Graph

> **Status:** Alpha (v0.2.0). The architecture is **intentionally
> layered but not yet complete** — see the **"What is NOT yet wired"
> matrix** at the bottom. The version was bumped from v0.1.0 to v0.2.0
> with the orchestrator state-model + cross-process cancel/resume work
> (PRs #53–#58); see `docs/evidence/tier-1-retrospective.md` for the
> release history.

This document is the system-level map for the `seharness` package.
For the orchestrator internals, see [`docs/architecture.md`](architecture.md).
For user-facing how-tos, see [`docs/user/`](user/).

## At a glance

`seharness` is a 12-phase workflow engine that turns a *feature request*
into a *draft pull request* by composing the following subsystems:

| Subsystem | Package | Responsibility |
|---|---|---|
| **Controller** | `seharness.controller` | Top-level orchestration, run lifecycle, state machine |
| **Orchestrator** | `seharness.orchestrator` | Phase sequencing (12 canonical phases, see SPEC) |
| **Sandbox** | `seharness.sandbox` | Isolated execution (Docker / subprocess / Noop) |
| **CI** | `seharness.ci` | GitHub PR/CI checks monitoring + readiness gates |
| **Observability** | `seharness.observability` | Trace records + secret redactor |
| **Artifacts** | `seharness.artifacts` | SBOM + traceability manifests |
| **Telegram** | `seharness.telegram` | Operator UI (slash commands + run buttons) |
| **Security** | `seharness.security` | Suspicious-payload filter (cluster H, story H2) |

All of these subsystem boundaries are enforced by **typed protocols**
in `src/seharness/<pkg>/types.py` and exercised by **mutation-killer
tests** in `tests/unit/<pkg>/test_*_mutation_killers.py`.

## High-level data flow

```
                 ┌─────────────────────────────────────────────┐
                 │               ENTRY POINTS                  │
                 ├─────────────────────────────────────────────┤
                 │  CLI (seharness run / /feature)             │
                 │  Telegram bot (/feature, /runs, /cancel)   │
                 │  Dashboard widget (POST /feature)           │
                 │  ControllerApplicationService.feature(...)  │
                 └────────────────────┬────────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────────┐
                 │            CONTROLLER (B + G)               │
                 │  - RunLedger  (idempotent state machine)    │
                 │  - ApplicationService (request routing)    │
                 │  - RealAdapters (production wiring)         │
                 └────────────────────┬────────────────────────┘
                                      │
                                      ▼
                 ┌─────────────────────────────────────────────┐
                 │          ORCHESTRATOR (A + E5/E6)           │
                 │  12-phase pipeline                          │
                 │  Phase 1: repository_discovery (B/C-3)      │
                 │  Phase 2: specification    (B-4)            │
                 │  Phase 3: planning          (B-5)            │
                 │  Phase 4: implementation   (B-6)            │
                 │  Phase 5: validation       (B-7)            │
                 │  Phase 6: remediation      (B-8)            │
                 │  Phase 7: review           (B-9)            │
                 │  Phase 8: draft_pr         (B-10)           │
                 │  Phase 9: ci               (B-11)           │
                 │  Phase 10: ready           (B-12)           │
                 │  Phase 11: completed       (B-13)           │
                 └────────────────────┬────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              │                       │                       │
              ▼                       ▼                       ▼
   ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
   │     SANDBOX (C)    │  │   CI / DASHBOARD    │  │ OBSERVABILITY (E5) │
   │  Docker | Subproc  │  │   (G + G12)         │  │  Trace + Redactor  │
   │  Noop              │  │  ChecksClient       │  │                    │
   │                    │  │  PullRequestClient  │  │  - SecretRedactor  │
   │  - Profile         │  │  Renderer           │  │  - TraceRecord     │
   │  - ProfileEnforce  │  │  Server             │  │                    │
   │  - Limits          │  │                    │  │  Captures all      │
   │                    │  │  GitHub Checks API  │  │  PipelineEvent     │
   │  /tmp isolation    │  │  CodeQL+Scorecard   │  │  emissions + red-  │
   │  cgroups           │  │  + Diff-Cover       │  │  acts secrets      │
   │  no network        │  │  + Mutation gate    │  │  before persistence│
   └────────────────────┘  └────────────────────┘  └────────────────────┘
              │                       │                       │
              └───────────────────────┼───────────────────────┘
                                      ▼
                 ┌─────────────────────────────────────────────┐
                 │       TELEGRAM (Operator UI)                │
                 │  - /feature  (start a run)                  │
                 │  - /runs     (list + status)                │
                 │  - /cancel   (kill a run)                   │
                 │  - /dashboard (link to live dashboard)      │
                 │  - /pr       (draft PR link)                │
                 │  - /resume   (resume paused run)            │
                 └─────────────────────────────────────────────┘
```

## Subsystem contracts

Each subsystem exports a **Protocol** (interface) + a **production
implementation** + a **fake/stub** for tests. Mutation-killer tests
prevent subsystems from calling outside their lane.

### Supporting packages (not subsystems)

These packages are referenced by subsystems but are not "the top 6"
of the orchestrator. They are listed here for completeness so every
package under `src/seharness/` is documented.

| Package | Role |
|---|---|
| `seharness.dashboard` | Live dashboard renderer + Pages server (G12) |
| `seharness.pipeline` | Legacy vertical-slice pipeline (Cluster A thin adapter) |
| `seharness.execution` | Task execution service (Cluster B) |
| `seharness.repository` | Repository profiler (Cluster B/C-3) |
| `seharness.phases` | Per-phase helper functions |
| `seharness.review` | Reviewer (Cluster B-9) |
| `seharness.delivery` | Branch/commit/PR backend abstractions |
| `seharness.validation` | Validation runner (Cluster B-7) |
| `seharness.domain` | Domain enums + dataclasses (RunState, etc.) |
| `seharness.models` | Model adapters: `codex`, `minimax`, `fake` |
| `seharness.telegram_runtime` | Bot runtime + command handlers (lower level than `telegram`) |

### Controller (`src/seharness/controller/`)

- **`application_service.py`** — `ControllerApplicationService` is the
  single entry point for "I want a feature implemented". It dispatches
  to the orchestrator when an orchestrator is wired in.
- **`run_ledger.py`** — `RunLedger` records every state transition.
  **Cluster E1** adds idempotency keys; **Cluster E2** adds optimistic
  concurrency. Currently uses an in-memory dict.
- **`real_adapters.py`** — the production wiring (TaskExecutor →
  SubprocessSandbox, CiMonitor → PyGithub, etc.).
- **`pause_resume.py`** — pause/resume support (used by E7 approval gates
  when added).

### Orchestrator (`src/seharness/orchestrator/`)

- **`orchestrator.py`** — the 12-phase state machine.
- **`phases.py`** — one function per phase (10 of 12; CI/ready/completed
  are integrated into the loop).
- **`runner.py`** — `LocalCommandRunner` (subprocess sandbox inside the
  orchestrator process; can be swapped for Docker).
- **`types.py`** — `OrchestratorConfig`, `PhaseResult`, terminal-state
  enums.

### Sandbox (`src/seharness/sandbox/`)

- **`docker.py`** — Docker backend (mounts only `/tmp/...`, drops caps,
  reads-only fs).
- **`subprocess_sandbox.py`** — pure-subprocess backend (faster; no
  isolation; for trusted inputs).
- **`profile.py`** — `SandboxProfile` (allowed_paths, denied_paths,
  env_allowlist, network_blocked, resource_limits).
- **`noop.py`** — no-op backend (used by unit tests).

### CI (`src/seharness/ci/`)

- **`checks.py`** — `ChecksClient` Protocol + `StubChecksClient`.
- **`monitor.py`** — `CiMonitor` polls PR checks until terminal state.
- **`polling.py`** — backoff + jitter for polling.
- **`readiness.py`** — `RequiredChecksView` enumerates required checks.

### Observability (`src/seharness/observability/`)

- **`trace.py`** — `TraceRecord` (per-run append-only event log) +
  `TraceWriter` (redact → serialize → write).
- **`redactor.py`** — `SecretRedactor` regex-based; used to scrub
  credentials from traces before persistence.

### Artifacts (`src/seharness/artifacts/`)

- **`store.py`** — on-disk artifact store (sha256-keyed JSON blobs).
- **`traceability.py`** — generates per-run traceability manifests
  (which inputs produced which outputs).

### Telegram (`src/seharness/telegram/`)

- **`service.py`** — `TelegramService` (slash-command dispatch).
- **`transport.py`** — `TelegramTransport` Protocol (send_message,
  send_photo, answer_callback_query).
- **`handlers.py`** — per-command handler functions.
- **`auth.py`** — allowlist-based authorization (chat_id → role).
- **`config.py`** — bot token, allowed chat ids.

## Run lifecycle (one feature request)

```
feature request submitted (CLI / Telegram / Dashboard)
    │
    ▼
ControllerApplicationService.feature_request(...)
    │  (1) creates RunLedger entry (idempotency key = feature text hash)
    │
    ▼
Orchestrator.start_run(...)
    │  (2) loops through 12 phases:
    │     • repository_discovery → RepositoryProfiler
    │     • specification        → TaskExecutionService
    │     • planning             → TaskExecutionService
    │     • implementation       → TaskExecutionService (in sandbox)
    │     • validation           → TaskExecutionService (in sandbox)
    │     • remediation          → TaskExecutionService (retry)
    │     • review               → Reviewer
    │     • draft_pr             → PullRequestClient
    │     • ci                   → CiMonitor (poll GitHub Checks)
    │     • ready                → (transition out of draft)
    │     • completed            → (terminal state)
    │
    │  (3) every phase emits a PipelineEvent → TraceRecord
    │
    ▼
Terminal state: completed | failed | blocked | paused
    │
    ▼
RunLedger state transition + Trace append + Telegram notification
```

## Storage layout

```
.openclaw-runs/orchestrator/<run_id>/
  ├── repo_profile.json
  ├── specification.json
  ├── plan.json
  ├── execution/<task_id>/
  │     ├── red/{command,stdout,stderr,result}.json
  │     └── green/{command,stdout,stderr,result}.json
  ├── review-verdict.json
  ├── trace.jsonl              # ← all PipelineEvents (redacted)
  └── artifacts/<sha256>.json   # ← SBOM entries
```

`docker/` is the per-run sandbox mirror (only when Docker is the
backend).

## What is NOT yet wired (Honesty matrix)

These are intentional, not bugs. Each row links to the cluster story
that will wire it up.

| Capability | Status | Owner cluster |
|---|---|---|
| Idempotency keys on RunLedger | **DONE (B — caller plumbing)** | E1 (P1) |
| Optimistic concurrency on RunLedger | **DONE (B — version counter + CAS)** | **E2** |
| Cross-process cancel-resume (state model) | **DONE (B — phase + ctx persistence on `RunRecord` + `FileRunLedger`; `Orchestrator.start_run(resume_from_run_id=...)` seam; spec-drift guard on resume) | **E3** |
| SQLite-backed durable ledger | NOT YET | B (P1) — currently in-memory only |
| Cancellation propagation to subprocess | **DONE (E4a primitive + E4b orchestrator plumbing)** | **E4** |
| Human-approval gates (pause + resume) | NOT YET | E7 (P1) |
| Schema migration framework | NOT YET | (separate future work; see [README §Status](https://github.com/johrenberger/software-engineering-harness#status)) |
| Real Codex adapter | NOT YET (fake only) | F (P1) |
| Real MiniMax adapter | NOT YET (fake only) | F (P1) |
| **Rate-limit retry-with-backoff in ModelRouter** | **DONE** | **H1** |
| **Suspicious-payload filtering** | **DONE** | **H2** |
| **PyPI release automation (release.yml)** | **DONE** (PyPI publish is best-effort; GitHub Release always ships; defended by 13 contract tests across `test_release_soft_publish.py`, `test_release_assets_pattern.py`, and the upstream-pin-resolver in `test_g4_actions_sha_pinning.py`) | **G9** |
| **Version drift check** | **DONE** | **G9** |
| Rate limiting on Telegram commands | NOT YET | (P2) |
| Multi-user auth (beyond allowlist) | NOT YET | (P2) |
| Distributed tracing (OTel) | NOT YET | (P2) |
| Branch protection on main | NOT YET | G19 (P2) |
| _Historical ref: G18 was the predecessor story to G9 (release automation); kept here for traceability._ | DONE | G9 (was G18) |

## Composition rule

A run **NEVER** bypasses the orchestrator. Every entry point (CLI,
Telegram, Dashboard, ControllerApplicationService) funnels through
`Orchestrator.start_run(...)` — the orchestrator is the only component
that emits pipeline events. This is the **layer-6 auto-merge
prevention** rule, enforced by `tests/unit/orchestrator/test_orchestrator_mutation_killers.py`.

## See also

- [`docs/architecture.md`](architecture.md) — orchestrator internals
- [`docs/user/run.md`](user/run.md) — running a feature end-to-end
- [`docs/user/sandbox.md`](user/sandbox.md) — sandbox profiles
- [`docs/user/traces.md`](user/traces.md) — trace records
- [`docs/engineering-dashboard.md`](engineering-dashboard.md) — G12 dashboard
- [`docs/evidence/`](evidence/) — PR-by-PR evidence files
