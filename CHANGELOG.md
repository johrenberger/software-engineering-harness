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

### Added — Cluster E (orchestrator internals)

- **E4a** (PR #49) — `CancellationToken` + `CancellationWatcher`
  primitives in `seharness.sandbox.cancellation`. 17 tests.
- **E4b** (PR #52) — cancel propagation through
  `LocalCommandRunner`/`Orchestrator`. `Orchestrator.start_run`
  registers a per-run `CancellationToken`;
  `Orchestrator.cancel_run(run_id)` flips the token (killing the
  in-flight subprocess via group SIGTERM→SIGKILL on the
  start_new_session group) before marking the ledger CANCELLED.
  Shell-parent/grandchild hang fixed by `start_new_session=True` +
  `os.killpg(SIGKILL)` on the entire process group. 22 tests.
- **E1** (PR #53) — idempotency keys on `RunLedger`. New field
  `RunRecord.idempotency_key` (default empty) + index
  `RunLedger._key_index`. `RunLedger.record_start(...,
  idempotency_key=)` dedupes by key (same key + same `run_id`
  → returns existing record; same key + different `run_id`
  → raises `IdempotencyKeyConflictError`). `Orchestrator.start_run`
  accepts `idempotency_key=` and threads it down. CLI
  `seharness run` exposes `--idempotency-key` (also wired to
  `SEHARNESS_IDEMPOTENCY_KEY`). `FileRunLedger` likewise accepts
  the kwarg and persists the key on the JSONL envelope so replays
  survive process restart. Scope: option B (caller plumbing) —
  persistence to durable SQLite store is not yet wired (Cluster B
  follow-up). 25 tests (14 ledger-level incl. FileRunLedger
  round-trip, 11 orchestrator-level).
- **E2** (PR #54) — optimistic concurrency on `RunLedger`.
  `RunRecord.revision: int = 1` (monotonic; bumped on every state
  transition AND on `record_start` re-keying). New exception
  `OptimisticConcurrencyError(run_id, expected_revision,
  actual_revision, expected_state, actual_state)` for stale CAS.
  Every `mark_*` accepts `expected_revision=` and/or `expected_state=`
  (semantic CAS); both must match if both supplied. Backward-compat:
  no `expected_*` arg preserves pre-E2 semantics. `FileRunLedger`
  mirrors the API and persists `revision` on the JSONL envelope so
  replays reconstruct it. Scope: option B (revision counter + CAS,
  no public API threading). Threading `expected_revision` into
  `Orchestrator` API and CLI is deferred to a follow-up if callers
  ask. 22 tests (14 in-memory ledger + 8 durable ledger round-trip).
- **E3** (PR #58) — orchestrator state model: phase + ctx + feature_description persistence on `RunRecord`, so `/resume <run_id>` can pick up a paused run across a process restart.
  - `RunRecord` gains `phase: str | None`, `ctx: dict[str, Any] | None`, `feature_description: str | None` (all `None` by default for back-compat).
  - New `to_jsonable(value)` helper coerces Pydantic models + nested containers into JSON-friendly forms (handlers store ctx via `to_jsonable(ctx)`).
  - New `RunLedger.record_phase(rid, *, phase, ctx, expected_revision=None)` advances the cursor atomically with E2 CAS. `FileRunLedger` mirrors the API and persists `phase` + `ctx` on the JSONL envelope (omitted when `None` to keep the format terse).
  - `Orchestrator.start_run` gains `resume_from_run_id: str | None`. When set:
    1. Looks up the persisted record; raises if unknown.
    2. Spec-drift guard: if the persisted `feature_description` differs from the new one, refuses the resume (callers should pick a fresh run_id).
    3. Refuses to resume from an unknown `phase` value (defensive against on-disk corruption).
    4. Falls back to "rerun from scratch" when `phase` is `None` (pre-E3 record).
    5. Re-uses the persisted `ctx` to rebuild `RunContext` and skips already-completed phases (continue semantics; not full replay).
  - After every phase (success OR failure), `Orchestrator.start_run` now calls `record_phase` so the cursor advances in lockstep with state transitions.
  - `Orchestrator.resume_run(run_id)` is upgraded: reads `feature_description` from the persisted record (caller no longer needs to remember it) and threads `resume_from_run_id=run_id` to `start_run`.
  - 29 new tests (13 in-memory ledger, 6 durable ledger, 10 orchestrator incl. spec-drift + unknown-phase + cross-process-style round-trip).
  - Honesty matrix E3 row: scope = B (in-process phase+ctx persistence + cross-process FileRunLedger durability; no SQLite yet — that's a Cluster B follow-up). "Schema migration framework" row removed (was mis-categorized).

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

### Changed — release-workflow hardening (shipped in 0.2.0)

The release pipeline shipped in this version went through five
follow-up commits before v0.2.0 cut cleanly. Each commit is
documented here so the next operator knows what was decided and
why.

- **`chore(release): make PyPI publish best-effort; GitHub Release
  always ships` (`bc45e30`, PR #55)** — operator model is now
  `tag = release`. Both `pypa/gh-action-pypi-publish` steps
  carry `continue-on-error: true`; a missing Trusted Publisher logs
  `NOT PUBLISHED` to `$GITHUB_STEP_SUMMARY` instead of cancelling
  the workflow. New `if: always()` `Publish status` step records
  outcome for the run UI. `github-release.needs` changed from
  `[build, publish-pypi]` to `[build]` — the release ships
  regardless of PyPI outcome. `environment: name: pypi` retained so
  a configured `pypi` environment still gates prod publishes when
  the user enables it. Honesty matrix G9 row updated to reflect the
  best-effort scope.
  - New `tests/unit/ci/test_release_soft_publish.py` — 6 contract
    tests guarding the soft-publish behaviour (continue-on-error on
    both publish steps, status-summary step exists, `github-release`
    does not depend on `publish-pypi`, tag-push gating preserved,
    header comment documents the policy).
  - `docs/releasing.md` gained a `tag = release` operator callout
    and a `Soft-publish behavior` subsection.

- **`fix(release): bump sigstore pin to 4.4.0 (3.0.1 was yanked)`
  (`c41677e`)** — `sigstore==3.0.1` was yanked from PyPI after the
  original G9 release shipped; bumped to latest stable (4.4.0),
  which preserves the `sign --bundle` CLI we depend on.

- **`fix(release): top-level attestations+id-token perms for build
  job` (`aaf1e54`)** — `actions/attest-build-provenance` (SLSA L1)
  and `sigstore sign` both perform OIDC exchanges that require
  `id-token: write` + `attestations: write`. These were only on the
  `publish-pypi` job; the `build` job needed them too. Promoted to
  the top-level `permissions:` block (same shape as `ci.yml`).

- **`fix(release): bump softprops/action-gh-release to v2.6.2 SHA
  + add force-push detector` (`e3d6a22`)** — the upstream
  `softprops/action-gh-release` repo force-pushed history on
  2026-07-13, invalidating the G4-pinned SHA `9d991d2c...`. Re-pinned
  to `v2.6.2` SHA `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65`.
  - New `test_pinned_shas_resolve_upstream` contract test (skipped
    by default; set `RUN_NETWORK_PIN_CHECK=1`) verifies every pinned
    SHA still resolves upstream. Uses `gh api` when authenticated
    (bypasses unauthenticated 60-req/h rate limit); gracefully
    degrades on HTTP 429 when run unauthenticated. Would have caught
    the force-push pre-merge.

- **`fix(release): use recursive glob for release asset attachments`
  (`7906802`)** — `actions/download-artifact` with
  `merge-multiple: true` extracts each artifact into a subdirectory
  named after the artifact. The original `softprops/action-gh-release`
  `files:` glob `dist-all/*.whl` did NOT descend into
  `dist-all/release-artifacts-3.12/dist/*.whl`, so the wheel +
  sdist + Sigstore bundles were silently dropped from the GitHub
  Release. Only the SBOM (a flat artifact) attached. Fixed by
  switching globs to `dist-all/**/*.whl` (recursive).
  - New `tests/unit/ci/test_release_assets_pattern.py` — 7 contract
    tests asserting the recursive globs are present and the bare
    non-recursive ones are absent. Would have caught the bug pre-merge.

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
