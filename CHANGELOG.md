# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - Cluster B (production adapters) + Cluster D (honest E2E)

### Added
- `seharness.controller.real_adapters`: production implementations
  of the slice-12 wiring slots.
  - `LocalTaskExecutor` — wraps slice-7 `TaskExecutionService`.
  - `GitHubChecksClient` — backs `ChecksClient` via `gh api`.
  - `FileRunLedger` — durable JSONL ledger with crash-safe replay.
- `examples/controller.yaml` defaults to the real adapters
  (`controller` instead of `stub`).
- `tests/e2e/test_real_vertical_slice.py` — 10 honest E2E tests
  asserting real artifacts, RED+GREEN evidence, draft PR, and
  durable ledger state. Proves the slice-13 simulation is gone.

### Auto-merge prevention
- Layer 7 added: `LocalTaskExecutor` exposes no `merge*` methods
  (enforced by mutation-killer).


## [Unreleased] - Cluster A (canonical orchestrator)

### Added
- `seharness.orchestrator` package: canonical workflow engine that
  composes the existing slice-3..slice-10 services in the SPEC §"Phase 8"
  sequence. Single entry point for `/feature`, `seharness run`, Telegram,
  dashboard, and the E2E test.
- `Orchestrator.start_run(feature_description, repo_path)` — runs the 12
  phases end-to-end, writes real artifacts under
  `<execution_root>/<run_id>/`, persists state in the shared `RunLedger`.
- `Orchestrator.resume_run` / `Orchestrator.cancel_run` — lifecycle API.
- `StubRunner` (default) and `LocalCommandRunner` (subprocess-gated by
  `OrchestratorConfig.use_real_subprocess`).
- `RunState.BLOCKED` and `RunLedger.mark_blocked()` for policy-halt runs.
- CLI `seharness run` now invokes the orchestrator (was: "not implemented").
- `docs/architecture.md` describing the orchestrator topology.

### Changed
- `VerticalSlicePipeline` is now a thin adapter over `Orchestrator.start_run`
  instead of a phase-name loop. PipelineEvent/PipelineResult shapes
  preserved; E2E test continues to pass.
- `ControllerApplicationService.feature_request` / `resume` / `cancel`
  delegate to the orchestrator when an `Orchestrator` instance is wired
  in (Cluster A story A3). `StubFeatureExecutor` retained for unit tests.
- Terminal-state phrasing aligned with SPEC §line 587 (`"completed"`).

### Auto-merge prevention
- Layer 6 added: `Orchestrator` exposes no `merge*` methods, enforced by
  `tests/unit/orchestrator/test_orchestrator_mutation_killers.py`.

## [0.1.0] - 2026-07-19

### Added — Slice 13 (hardening + production wiring + production deploy)

- Production Telegram bot runtime (`harness-telegram-bot` console script) wrapping `python-telegram-bot>=21.0`.
- Live aiohttp dashboard server (`harness-dashboard` console script) on `127.0.0.1:8765`.
- OpenClaw skill registry with 8 per-command skills (`harness-feature`, `harness-status`, `harness-runs`, `harness-resume`, `harness-cancel`, `harness-pr`, `harness-help`, `harness-dashboard`).
- End-to-end vertical-slice pipeline (`seharness.pipeline.vertical_slice`) covering all 12 SPEC phases.
- Dockerfile (`docker/Dockerfile`) + docker-compose.yml with `harness-bot` + `harness-dashboard` services.
- GitHub Actions CI workflow at `.github/workflows/ci.yml`.
- `.env.example` with `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, etc.
- `examples/controller.yaml` factory example.
- `docs/user/{install,configure,run,extend}.md`.

### Added — Slice 12 (OpenClaw packaging)

- `seharness.controller` package: `RunLedger`, `Pauser`/`Resumer` Protocols + Stub impls, `ControllerApplicationService`, `ApplicationServiceFactory`.
- `seharness.dashboard` package: `DashboardRenderer`, `DashboardSnapshot`, `GitCommit`, `render_text_summary`.

### Added — Slice 11 (Telegram ingress)

- `seharness.telegram` package: `TelegramAuthorizer`, `Redactor`, `CommandParser`, `CommandDispatcher`, `StubApplicationService`, `TelegramTransport` Protocol + `TelegramBotTransport` real impl.

### Added — Slice 10 (CI monitoring)

- `seharness.ci` package: `ChecksClient`, `RequiredChecksView`, `ReadyEvaluator`, `CiMonitor`, `CiRemediationLoop`. Auto-merge prevention (structural + runtime AST scan layers).

### Added — Slice 9 (Git delivery)

- `seharness.delivery` package: `Branch`, `Commit`, `IdempotencyToken`, `PR`, `DeliveryService`, `DeliveryGate`.

### Added — Slice 8 (Independent review)

- `seharness.review` package: `Reviewer`, `Finding`, `Coverage`, `ReviewPolicy`. Fresh-context review (no parent-session memory).

### Added — Slice 7 (Validation + remediation)

- `seharness.validation` package: `ValidationRunner`, `FailureClassifier`, `RemediationController`, `RetryBudget`, `BoundedEvidence`.

### Added — Slice 6 (TDD-aware task execution)

- `seharness.execution` package: `TaskExecutionService`, `Evidence`, `PathControls`, `Completion`. RED-before-GREEN enforcement.

### Added — Slice 5 (Specification + planning)

- `seharness.phases.specification`, `seharness.phases.planning`, `seharness.phases.impact`. `Traceability` + `Plan` Pydantic models.

### Added — Slice 4 (Model contracts)

- `seharness.models` package: `ModelRouter`, `FakeAdapter`, `MiniMaxAdapter`, `CodexAdapter`, `OutputRepair`, `NormalizedFailure`.

### Added — Slice 3 (Repository discovery)

- `seharness.repository` package: `RepositoryProfile`, `CommandResolver`, `BaselineRecorder`, `FrameworkNeutralDiscovery`.

### Added — Slice 2 (State + persistence)

- `seharness.state_machine` + `seharness.artifacts.store`: `PhaseState` StrEnum, `PhaseTransitionError`, `AtomicStateStore`, `ResumableStateRepository`.

### Added — Slice 1 (Configuration validation)

- `seharness.config`: `HarnessConfig`, `ModelRoutingConfig`, `FeatureConfig`. Strict unknown-key rejection. CLI precedence over env over file.

## [0.0.0] - 2026-07-18

Project scaffolded.
