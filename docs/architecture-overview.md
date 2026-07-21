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
| **Security** | `seharness.security` | Suspicious-payload filter + secret redactor (`docs/user/sandbox.md` for the threat model). |

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
| `seharness.dashboard` | Live dashboard renderer + Pages server (see `docs/engineering-dashboard.md`). |
| `seharness.pipeline` | Legacy thin adapter for backward compatibility. |
| `seharness.execution` | Task execution service. |
| `seharness.repository` | Repository profiler. |
| `seharness.phases` | Per-phase helper functions. |
| `seharness.review` | Reviewer. |
| `seharness.delivery` | Branch/commit/PR backend abstractions. |
| `seharness.validation` | Validation runner. |
| `seharness.domain` | Domain enums + dataclasses (RunState, etc.). |
| `seharness.models` | Model adapters: `codex`, `minimax`, `fake`. |
| `seharness.telegram_runtime` | Bot runtime + command handlers (lower level than `telegram`). |

### Controller (`src/seharness/controller/`)

- **`application_service.py`** — `ControllerApplicationService` is the
  single entry point for "I want a feature implemented". It dispatches
  to the orchestrator when an orchestrator is wired in.
- **`run_ledger.py`** — `RunLedger` records every state transition with
  idempotency keys and optimistic concurrency. `FileRunLedger`
  persists across process restarts.
- **`real_adapters.py`** — the production wiring (TaskExecutor →
  SubprocessSandbox, CiMonitor → PyGithub, etc.).
- **`pause_resume.py`** — pause/resume support (used by the human-
  approval gates when added).

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

These are intentional, not bugs. Each row links to the module or
doc that will wire it up.

| Capability | Status | Owner |
|---|---|---|
| Idempotency keys on RunLedger | **DONE** — caller plumbing for `feature_request` | `src/seharness/controller/run_ledger.py` |
| Optimistic concurrency on RunLedger | **DONE** — version counter + CAS | `src/seharness/controller/run_ledger.py` |
| Cross-process cancel-resume (state model) | **DONE** — phase + ctx persistence on `RunRecord` + `FileRunLedger`; `Orchestrator.start_run(resume_from_run_id=...)` seam; spec-drift guard on resume | `src/seharness/orchestrator/orchestrator.py` |
| SQLite-backed durable ledger | NOT YET | (P1 follow-up) — currently in-memory only |
| Cancellation propagation to subprocess | **DONE** — cancellation token + subprocess process group kill | `src/seharness/orchestrator/orchestrator.py` |
| Worker leases + abandoned-run recovery | **DONE** — `LeaseStore` with TTL + `recover_expired` at start of every run | `src/seharness/orchestrator/leases.py` |
| Per-axis budget tracking (model tokens, cost, time, retries, files, diff size) | **DONE** — `BudgetTracker` enforced at every phase boundary; production profile refuses unlimited budgets | `src/seharness/orchestrator/budgets.py` |
| OTLP-shaped trace spans + secret redaction | **DONE** — `Tracer` + `SecretRedactor`; `NullTracer` is the in-process default | `src/seharness/orchestrator/telemetry.py`, `src/seharness/observability/redactor.py` |
| Human-approval gates (pause + resume) | NOT YET — handler surface exists; policy layer is design-stage | (P1 follow-up) |
| Schema migration framework | NOT YET | (separate future work; see [README §Status](https://github.com/johrenberger/software-engineering-harness#status)) |
| Real Codex adapter | NOT YET (fake only) | (P1 follow-up) |
| Real MiniMax adapter | NOT YET (fake only) | (P1 follow-up) |
| Rate-limit retry-with-backoff in ModelRouter | **DONE** | `src/seharness/models/router.py` |
| Suspicious-payload filtering | **DONE** | `src/seharness/security/payload_filter.py` |
| PyPI release automation (release.yml) | **DONE** — PyPI publish is best-effort; GitHub Release always ships; defended by 13 contract tests | `.github/workflows/release.yml` |
| Version drift check | **DONE** | `src/seharness/release/version_check.py` |
| Rate limiting on Telegram commands | NOT YET | (P2) |
| Multi-user auth (beyond allowlist) | NOT YET | (P2) |
| Distributed tracing (OTel wire protocol) | NOT YET — local JSONL only today | (P2) |
| Branch protection on main | NOT YET | (P2) |

## Production trust model

The production profile (`seharness.orchestrator.runtime_profile.ProductionProfile`)
is the **only** profile that drives a real LLM run. The trust model
the harness enforces is:

1. **Fail-closed on stubs.** A profile that names a stub adapter for
   a critical phase (planning, implementation, validation, review,
   delivery) refuses to start. The `ProductionProfile.fail_closed()`
   validator raises `RuntimeError` if any critical phase is wired to
   a fake. See `src/seharness/orchestrator/runtime_profile.py`.
2. **Fail-closed on missing budgets.** The production profile refuses
   to construct with `budgets=None` or with any axis set to `None`.
   Operators MUST set per-axis ceilings (model tokens, cost, time,
   retries, files, diff size) or the run is rejected before phase 0.
3. **Sandbox deny-by-default.** The default `SandboxProfile` has
   empty `allowed_paths`, empty `allowed_env`, `network_mode="none"`,
   and a fixed deny list of canonical secret env vars (`OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `AWS_*`, `PATH`, `HOME`, ...).
   Users can ADD to the deny list but cannot remove entries. See
   `docs/user/sandbox.md`.
4. **Lease-protected runs.** A run starts by acquiring a worker
   lease (`LeaseStore.acquire`). A crashed worker's lease auto-expires
   after `default_lease_ttl_seconds()`; `recover_expired()` clears
   abandoned leases at every start so a new worker can resume.
   Two concurrent workers cannot advance the same `run_id` + `revision`.
5. **Budget enforcement at every phase.** `BudgetTracker.enforce()`
   runs after each phase. Exhaustion translates to
   `PhaseOutcome.BLOCKED` with a human-readable reason. The run
   pauses and waits for an operator decision (extend budget, cancel,
   or resume with a tighter scope).
6. **Secret-redacted traces.** Every string field in every trace
   event is scrubbed by `SecretRedactor` before it lands on disk
   or in the OTLP payload. The sentinel `***REDACTED***` is grep-able.
7. **No bypass of the orchestrator.** Every entry point (CLI,
   Telegram, Dashboard, ControllerApplicationService) funnels through
   `Orchestrator.start_run(...)`. The orchestrator is the only
   component that emits pipeline events. This is the **layer-6
   auto-merge prevention** rule, enforced by
   `tests/unit/orchestrator/test_orchestrator_mutation_killers.py`.

### What "fail-closed" does NOT mean

* The harness does NOT refuse to start when an operator uses a stub
  provider in **development** profile (the `DevelopmentProfile` is
  the explicit, opt-in override for local testing).
* The harness does NOT refuse to start when an operator runs without
  a Telegram bot token (Telegram intake is optional; the orchestrator
  can be driven via CLI or HTTP).
* The harness does NOT refuse to start without network egress —
  `SandboxProfile.network_mode="none"` is the default and the run
  proceeds; only when the profile explicitly enables `bridge` mode
  does the allowlist become active.

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
