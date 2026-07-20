# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
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

## [0.2.0] - 2026-07-20

### Added — Cluster H (rate-limit + payload hardening)

- `RetryPolicy` class in `seharness.models.router` with bounded
  exponential backoff (`max_attempts`, `initial_backoff_s`,
  `max_backoff_s`, injectable `sleeper`). `ModelRouter` consults it on
  `rate_limit` (`ErrorKind`) errors before falling back.
- `ErrorKind` Literal extended with `"rate_limit"` (fifth canonical kind).
- New `seharness.security` package: `SuspiciousPayloadFilter` rejects
  payloads with closed `FilterReason` reasons (`zero_length`, `too_long`,
  `null_bytes`, `control_characters`, `excessive_control_characters`,
  `binary_content`, `prompt_injection_marker`). Tunable via
  `PayloadFilterConfig`. Rejected verdicts include a sanitised preview
  so callers can log safely.

### Added — Cluster F1 + I3 (honest docs)

- `docs/providers.md` — provider & credentials reference. Documents the
  fail-closed behaviour of `FakeMiniMaxAdapter` + `FakeCodexAdapter`
  and the honesty matrix: `providers.toml` and env-var credentials
  are NOT yet wired (P1 follow-up).
- `docs/operations.md` — operator runbook: container lifecycle, log
  forensics, ledger replay, rollback, observability surfaces, incident
  playbook.
- Contract tests for both: `tests/unit/docs/test_providers_md.py` (13
  tests) + `tests/unit/docs/test_operations_md.py` (15 tests). The
  architecture-overview contract test (`test_doc_lists_all_subsystem_packages`)
  automatically forces updates when a new subsystem package ships.

### Added — Cluster E4a (cancellation primitive)

- `CancellationToken` + `CancellationWatcher` + `install_sigint_handler`
  in `seharness.sandbox.cancellation`. `SubprocessSandbox.run` accepts
  `cancel=` + `cancel_grace_seconds=`; SIGTERM → grace window →
  SIGKILL escalation. `SandboxResult.cancelled: bool` field.
- `tests/unit/sandbox/test_cancellation.py` — 17 tests.
- Orchestrator-phase plumbing (E4b) and CLI SIGINT integration
  deferred to a follow-up.

### Added — Cluster G9 (release automation)

- `.github/workflows/release.yml` — tag-driven pipeline: build wheel
  + sdist, generate CycloneDX SBOM, sign with Sigstore (keyless via
  OIDC), publish to PyPI via Trusted Publishers, attach all artifacts
  to a GitHub Release. Pre-release tags (`vX.Y.Z-rcN`) route to TestPyPI.
- `scripts/check_version_drift.py` — cross-file version drift checker.
  Fails CI if `pyproject.toml`, `__version__`, or CHANGELOG disagree.
  Used by `release.yml::verify-version` and the unit suite.
- `tests/unit/scripts/test_check_version_drift.py` — 19 tests.
- `docs/releasing.md` updated to point at the automated pipeline;
  manual steps retained as a fallback.

### Changed

- Cluster B + Cluster D promoted from `[Unreleased]` into `[0.2.0]`:
  production adapter implementations (`LocalTaskExecutor`,
  `GitHubChecksClient`, `FileRunLedger`), 10-test honest E2E vertical
  slice, and 7-layer auto-merge prevention.
- `pyproject.toml` version bumped `0.1.0 → 0.2.0`.
- `seharness.__init__.py.__version__` bumped to `0.2.0`.

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
