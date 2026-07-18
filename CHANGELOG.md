# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
