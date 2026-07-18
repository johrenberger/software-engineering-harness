# Slice 13 — Hardening + Production wiring + Production deploy

## Scope

Per SPEC §23 (amendment; this slice extends the SPEC's 12-PR sequence).

### Part A — Hardening (real wiring)
1. Real `python-telegram-bot` integration (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_CHAT_IDS` env, polling mode, graceful SIGINT).
2. OpenClaw SkillRegistry with 8 per-command skills.
3. Live aiohttp DashboardServer on `127.0.0.1:8765` with `/` + `/api/state` + `/healthz` (rejects non-loopback bind).
4. CommandDispatcher routes 9 commands to ApplicationService.
5. E2E Phase-8 vertical-slice pipeline.

### Part B — Production deploy
6. Dockerfile (python:3.13-slim, non-root USER, HEALTHCHECK, EXPOSE 8765).
7. docker-compose.yml (`harness-bot` + `harness-dashboard` services; dashboard bind loopback-only).
8. GitHub Actions CI workflow (`.github/workflows/ci.yml`).
9. pyproject.toml setuptools backend + console scripts (`seharness`, `harness-telegram-bot`, `harness-dashboard`).
10. CHANGELOG.md (Keep-a-Changelog), README.md, docs/user/{install,configure,run,extend}.md, examples/controller.yaml, .env.example.

## Deliverables

### Source (8 new modules)
| Path | Purpose |
| --- | --- |
| `src/seharness/telegram_runtime/__init__.py` | public surface |
| `src/seharness/telegram_runtime/bot_runtime.py` | `TelegramBotRuntime`, `cli()` |
| `src/seharness/telegram_runtime/command_handlers.py` | `CommandDispatcher`, `_StubUpdate` |
| `src/seharness/dashboard/server.py` | `DashboardServer`, `DashboardState`, `cli()` |
| `src/seharness/skills/__init__.py` | public surface |
| `src/seharness/skills/registry.py` | `SkillRegistry`, `SkillManifest` |
| `src/seharness/pipeline/__init__.py` | public surface |
| `src/seharness/pipeline/vertical_slice.py` | `VerticalSlicePipeline`, `PipelineEvent`, `PipelineResult` |
| `src/seharness/skills/harness-*/SKILL.md` | 8 OpenClaw skill manifests |

### Tests (8 new files, 83 tests)
| File | Tests |
| --- | --- |
| `tests/unit/telegram_runtime/test_bot_runtime.py` | 10 |
| `tests/unit/telegram_runtime/test_command_handlers.py` | 12 |
| `tests/unit/dashboard/test_dashboard_server.py` | 10 |
| `tests/unit/skills/test_skill_manifests.py` | 8 |
| `tests/unit/deploy/test_dockerfile.py` | 16 |
| `tests/unit/deploy/test_packaging.py` | 14 (1 pre-existing-pass) |
| `tests/e2e/test_vertical_slice.py` | 4 |
| `tests/unit/telegram_runtime/test_runtime_mutation_killers.py` | 9 |

### Deployment artifacts
- `docker/Dockerfile` (python:3.13-slim, non-root, HEALTHCHECK, EXPOSE 8765)
- `docker/docker-compose.yml` (harness-bot + harness-dashboard, env_file, loopback bind)
- `.github/workflows/ci.yml` (ruff + mypy + bandit + pip-audit + pytest on push to main + PRs)
- `examples/controller.yaml` (factory example)
- `.env.example` (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_IDS, etc.)
- `CHANGELOG.md` (Keep-a-Changelog with 12 slice entries + slice 13)
- `README.md` (install + container)
- `docs/user/{install,configure,run,extend}.md`

### Modified
- `pyproject.toml` (added aiohttp, python-telegram-bot; 3 console scripts)
- `src/seharness/telegram/auth.py` (Redactor regex `{25,}` → `{20,}` to match real bot tokens which are 24 chars in test fixtures)

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 176 files clean |
| `ruff check` | pre-existing slice-8-12 issues only; slice 13 clean |
| `mypy --strict` | 81 source files clean |
| `bandit` | 7 low (B101 assert_used, accepted) |
| `pip-audit` | clean (seharness itself not on PyPI yet, but deps clean) |
| `pytest --no-cov` | **1099/1099** (was 1016 + 83 new) |
| `mutmut 2.0` | see `final-gate/mutation/result.json` |

## Architectural notes

- **`TelegramBotRuntime`** is frozen dataclass, validates token in `__post_init__`, does NOT start polling on construction, exposes `run()` + `install_handlers()`.
- **`CommandDispatcher`** is a thin shell: parse → call ApplicationService method → bound reply. No workflow logic.
- **`DashboardServer`** rejects `0.0.0.0` and `::` binds (security: SPEC §22). Loopback-only.
- **`SkillRegistry.default()`** discovers skills from `src/seharness/skills/<name>/SKILL.md` at runtime.
- **`VerticalSlicePipeline`** is the slice-13 wiring for SPEC §"Phase 8"; emits 12 events (feature_request → completed).

## Auto-merge prevention (5 layers, slice 13)
1. Slice 10: `ChecksClient` Protocol has no merge methods.
2. Slice 11: `ApplicationService` Protocol has no merge methods; `/pr` handler scans outgoing message.
3. Slice 12: `ControllerApplicationService` + `DashboardRenderer` + `StubPauser` + `StubResumer` all have no merge methods.
4. Slice 13: `TelegramBotRuntime` + `CommandDispatcher` + `DashboardServer` + `SkillRegistry` + `VerticalSlicePipeline` all have no merge methods (mutation killers).
5. Test-level: `test_*_no_merge_methods` + `test_pr_message_never_contains_merge_commands` + `test_dashboard_no_merge_buttons_rendered`.

## PR table (final)

| PR | Slice | Commit |
|----|-------|--------|
| #1  | 01-configuration-validation | 1ef2fa5 |
| #2  | 02-state-and-persistence    | ff41f6a |
| #3  | 03-repository-discovery     | e1f614a |
| #4  | 04-model-contracts          | 11566ca |
| #5  | 05-specification-planning   | e4179f4 |
| #6  | 06-tdd-task-execution       | e1411b2 |
| #7  | 07-validation-remediation   | 92fdea1 |
| #8  | 08-independent-review       | 744f271 |
| #9  | 09-git-delivery             | 2a5667d |
| #10 | 10-ci-monitoring            | 33d805b |
| #11 | 11-telegram-ingress         | 9e62a51 |
| #12 | 12-openclaw-packaging       | 9cd4831 |
| #13 | 13-hardening-production-deploy | (this PR) |
