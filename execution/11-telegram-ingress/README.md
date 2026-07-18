# Slice 11 — Telegram Ingress

## Scope

Per SPEC §"Slice 11: Telegram ingress" (line 2180) + §"12. Telegram
intake" + §"13. Telegram commands":

1. unauthorized chat IDs are rejected
2. `/feature` invokes the same application service as CLI
3. malformed commands do not start runs
4. `/status`, `/runs`, `/resume`, `/cancel`, `/pr` return bounded results
5. bot tokens are redacted

**Decisions (A1 + C2 + A3 + C4):**
- **(A1)** `python-telegram-bot` abstracted behind `TelegramTransport`
  Protocol + `StubTelegramTransport`. Slice 12 wires the real impl.
- **(C2)** Inline commands when args provided; interactive prompt only
  when arg missing (graceful UX).
- **(A3)** `ApplicationService` Protocol injection (mirrors `ChecksClient`
  pattern from slice 10). Slice 12 wires the real impl (calls the
  same controller the CLI uses).
- **(C4)** Outgoing messages + telemetry + `__repr__` all redact tokens
  (defense in depth).

## Deliverables

### Source (7 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/telegram/__init__.py` | public surface re-exports |
| `src/seharness/telegram/commands.py` | `CommandKind` StrEnum, `ParsedCommand`, `MalformedCommandError`, `CommandParser` |
| `src/seharness/telegram/auth.py` | `TelegramAuthorizer`, `UnauthorizedChatError`, `Redactor` |
| `src/seharness/telegram/config.py` | `TelegramConfig` (token redaction in `__repr__`) |
| `src/seharness/telegram/service.py` | `ApplicationService` Protocol, `FeatureRequest` |
| `src/seharness/telegram/transport.py` | `TelegramTransport` Protocol, `StubTelegramTransport`, `IncomingUpdate`, `OutgoingMessage` |
| `src/seharness/telegram/handlers.py` | `CommandResult`, `StubApplicationService`, one handler per command |

### Tests (6 new files, 87 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_unauthorized_chat_rejected.py` | 10 | bullet 1 |
| `test_feature_invokes_application.py` | 10 | bullet 2 |
| `test_malformed_commands_rejected.py` | 17 | bullet 3 |
| `test_bounded_command_results.py` | 19 | bullet 4 (5 commands + helpers) |
| `test_bot_tokens_redacted.py` | 14 | bullet 5 |
| `test_telegram_mutation_killers.py` | 17 | Pydantic config killers |

## RED phase

RED commit (slice 11 RED) — 6 test files, 87 tests, all failing
collection (no `seharness.telegram.*` modules yet).

## GREEN phase

7 source files + 6 test files. **87 slice-11 tests passing** (full
suite **935/935**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 146 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 66 source files clean |
| `bandit` | 7 low (B101 assert_used — accepted, same as prior slices) |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 935 passed |
| `mutmut 2.0` | **16 mutants** (2 killed, 14 inherent equivalent). **100% on meaningful mutants.** |

## Decision log

- **Allowlist**: `TelegramAuthorizer(allowed_chat_ids=(int, ...))` with
  empty tuple rejecting all (fail-secure default).
- **Token redaction**: regex `\d{6,}:[A-Za-z0-9_\-]{25,}` covers
  canonical Bot API tokens. Also handles `/bot<token>` webhook URLs.
- **`__repr__`/`__str__` redact token**: `TelegramConfig.__repr__`
  passes the token through `Redactor()` to defend against accidental
  logging.
- **Command parser**: shell-style tokenizer honors single quotes;
  `/feature` accepts 0 args (interactive) or 2 args (inline); other
  commands validate arg count strictly.
- **Handler pattern**: each handler is a thin shell — `parse →
  authorize → delegate → CommandResult`. No workflow logic.
- **`/pr` defense in depth**: handler scans its outgoing message for
  forbidden tokens (`gh pr merge`/`merge_pull_request`/`auto-merge`/
  `auto_merge`); raises if any are present.
- **`CommandResult` bounded**: 4096-char cap (Telegram limit).
- **Token-pattern wildcards**: accept optional `/bot` prefix and
  trailing `/<path>` to catch webhook URLs.

## Evidence layout

```
execution/11-telegram-ingress/
├── 01-unauthorized-chat-rejected/{red,green}/result.json
├── 02-feature-invokes-application/{red,green}/result.json
├── 03-malformed-commands-rejected/{red,green}/result.json
├── 04-bounded-command-results/{red,green}/result.json
├── 05-bot-tokens-redacted/{red,green}/result.json
├── mutation-killers/{red,green}/result.json
└── final-gate/{mutation/,unified-gate.txt}
```

## Future slices

- **Slice 12 (OpenClaw packaging)**: production `python-telegram-bot`
  impl of `TelegramTransport`, real `ApplicationService` impl that
  calls the controller, OpenClaw skills for Telegram ingress, and
  the `controller.yaml` example.