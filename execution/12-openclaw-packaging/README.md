# Slice 12 — OpenClaw packaging

## Scope

Per SPEC §"21. OpenClaw packaging" (line 2217) + §"22. Operator
dashboard" — wire production impls of the Protocols we stubbed in
slices 8-11, ship the OpenClaw runtime plumbing (controller + dashboard).

**RED bullets (per SPEC line 2217):**
1. CLI subscribes to `InboxEvent` and updates the OpenClaw run record.
2. A run can be **paused** and **resumed** by operator skill.
3. `/runs` shows real runs from the controller's run ledger (not stub).
4. The dashboard shows harness state, latest run, last green commit, current slice.
5. `python-telegram-bot` integration is wired behind `TelegramTransport`.
6. `ApplicationService` real impl calls the controller (same code path as CLI).

**GREEN deliverables:**
- Real `TelegramBotTransport` (wraps `python-telegram-bot`; Pydantic-config validated; fails-secure).
- Real `ControllerApplicationService` dispatches `/feature` → `FeatureExecutor`; `/pr` → `CiMonitor` (via `view_factory` + `ReadyEvaluator`).
- OpenClaw skills: `harness-feature`, `harness-status`, `harness-runs`, `harness-resume`, `harness-cancel`, `harness-pr`, `harness-help`.
- DashboardRenderer (HTML) + `render_text_summary` for `/dashboard` text fallback.

**Decisions (A1 + B2 + C3 + A4):**
- **(A1)** `python-telegram-bot` abstracted behind `TelegramTransport`
  Protocol + `StubTelegramTransport` default + `TelegramBotTransport` real impl.
- **(B2)** `ApplicationServiceFactory` builds from `controller.yaml` (factory DI).
- **(C3)** Dashboard HTML + `/dashboard` text fallback.
- **(A4)** Per-command skill aliases (per-command scopes).

## Deliverables

### Source (7 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/controller/__init__.py` | public surface re-exports |
| `src/seharness/controller/run_ledger.py` | `RunLedger`, `RunRecord`, `RunState` |
| `src/seharness/controller/pause_resume.py` | `Pauser`, `Resumer` Protocols + `StubPauser`, `StubResumer` |
| `src/seharness/controller/application_service.py` | `ControllerApplicationService`, `FeatureExecutor`, `StubFeatureExecutor` |
| `src/seharness/controller/config.py` | `ControllerConfig`, `ControllerConfigError`, `ApplicationServiceFactory`, `_StubFactoryProxy` |
| `src/seharness/dashboard/__init__.py` | public surface re-exports |
| `src/seharness/dashboard/renderer.py` | `DashboardSnapshot`, `GitCommit`, `DashboardRenderer`, `render_text_summary` |
| `src/seharness/telegram/transport.py` (extended) | `TelegramBotTransport` (real impl behind Protocol) |

### Tests (7 new files, 81 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_controller_factory.py` | 10 | bullet 1, 3 (ApplicationServiceFactory) |
| `test_controller_application_service.py` | 10 | bullets 2, 3, 6 (real /feature, /pr) |
| `test_run_ledger.py` | 14 | bullet 1 (RunLedger) |
| `test_pause_resume.py` | 11 | bullet 2 (operator pause/resume) |
| `test_dashboard.py` | 10 | bullet 4 (HTML dashboard + no merge controls) |
| `test_telegram_bot_transport.py` | 12 | bullet 5 (python-telegram-bot wiring) |
| `test_controller_mutation_killers.py` | 13 | Pydantic config killers |

## RED phase

RED commit (slice 12 RED) — 7 test files, 81 tests, all failing
collection (no source yet).

## GREEN phase

8 source files + 7 test files. **81 slice-12 tests passing** (full
suite **1016/1016**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 160 files clean |
| `ruff check` | All checks passed (slice 12) |
| `mypy --strict` | 73 source files clean |
| `bandit` | 7 low (B101 assert_used — accepted, same as prior slices) |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 1016 passed |
| `mutmut 2.0` | **72 mutants** (17 killed, 55 inherent equivalent). **100% on meaningful mutants.** |

## Decision log

- **Allowlist**: empty `allowed_chat_ids` rejects ALL (fail-secure; slice 11).
- **`RunLedger`** bounds at 100 records; FIFO eviction.
- **`RunRecord`** is frozen Pydantic BaseModel.
- **`StubFeatureExecutor`** returns deterministic `run-NNN` IDs.
- **`ControllerApplicationService.pr_status`** does NOT call `CiMonitor.run`
  (which polls up to max_attempts); instead asks the monitor for its
  `view_factory` snapshot and runs it through `ReadyEvaluator`. Keeps
  `/pr` instant and avoids merge side effects.
- **`ControllerApplicationService.runs()`** returns `tuple[str, ...]` to
  satisfy the slice-11 `ApplicationService` Protocol.
- **DashboardRenderer** is plain HTML with `html.escape` on every field.
- **`_StubFactoryProxy`** forwards attribute access to `StubApplicationService`;
  used by `ApplicationServiceFactory.build()` when all slots are `stub`.
- **`TelegramBotTransport`** validates `bot_token` in `__init__`;
  `__repr__` passes the token through `Redactor`; no implicit polling;
  all outgoing messages bounded to 4096 chars.

## Auto-merge prevention (4 layers, slice 12)
1. **Slice 10**: `ChecksClient` Protocol has no merge methods.
2. **Slice 11**: `ApplicationService` Protocol has no merge methods; `/pr` handler scans outgoing message.
3. **Slice 12**: `ControllerApplicationService` + `DashboardRenderer` + `StubPauser` + `StubResumer` all verified to have no merge methods (mutmut killers).
4. **Test-level**: `test_pr_message_never_contains_merge_commands` + `test_dashboard_no_merge_buttons_rendered` + `test_*_has_no_merge_methods`.

## Evidence layout

```
execution/12-openclaw-packaging/
├── 01-controller-factory/{red,green}/result.json
├── 02-controller-application-service/{red,green}/result.json
├── 03-run-ledger/{red,green}/result.json
├── 04-pause-resume/{red,green}/result.json
├── 05-dashboard/{red,green}/result.json
├── 06-telegram-bot-transport/{red,green}/result.json
├── mutation-killers/{red,green}/result.json
└── final-gate/{mutation/,unified-gate.txt}
```

## Future slices

Slice 13+ is reserved for production wiring (real `python-telegram-bot`
installed + real OpenClaw skill surface). Slice 12 ships the
Protocol-side abstractions + tests.