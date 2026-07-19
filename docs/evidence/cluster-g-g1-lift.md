# G1 — Coverage floor lift (88 → 89)

**Status:** ✅ MERGED
**Branch:** `agent/g1-lift-coverage` → `main`
**PR:** [#34](https://github.com/johrenberger/software-engineering-harness/pull/34) — merged at `c4cda1f`
**Commit:** `d7d019c` — `feat(coverage): G1 lift fail_under 88 -> 89 with new tests`

## What landed

The `fail_under` floor was lifted from **88% to 89%**, with all the new
test surface required to clear the gate on `main`. The CI gate now
rejects any PR that drops combined coverage below 89%.

## Coverage progression

| Phase | Coverage | Δ |
|---|---|---|
| Pre-Tier-1 baseline | 88.22% | — |
| After transport.py tests | 88.62% | +0.40 pp |
| After checks.py + service.py tests + pragma:no cover | 88.99% | +0.37 pp |
| After runner.py tests | 89.17% | +0.18 pp |
| **Net G1 lift** | **+0.95 pp** | floor lifted to 89 |

## Per-file lift

| File | Before | After | Δ | Mechanism |
|---|---|---|---|---|
| `telegram/transport.py` | 72% | 97% | +25 pp | dispatch error-path tests |
| `orchestrator/runner.py` | 76% | 100% | +24 pp | full LocalCommandRunner + StubRunner tests |
| `telegram/service.py` | 77% | 100% | +23 pp | Protocol-body pragma:no cover |
| `ci/checks.py` | 79% | 97% | +18 pp | Protocol-body pragma + StubChecksClient tests |

## Files touched

- `pyproject.toml` — bumped `fail_under`, updated comment block.
- `src/seharness/telegram/transport.py` — pragma additions (3 lines).
- `src/seharness/telegram/service.py` — pragma additions (6 lines).
- `src/seharness/ci/checks.py` — pragma addition (1 line).
- `tests/unit/telegram/test_stub_transport.py` — NEW (6 tests).
- `tests/unit/ci/test_stub_checks_client_coverage.py` — NEW (7 tests).
- `tests/unit/orchestrator/test_runner_coverage.py` — NEW (11 tests).
- `tests/unit/ci/test_g1_lift_coverage_workflow.py` — NEW (9 contract tests).
- `tests/unit/controller/test_telegram_bot_transport.py` — +6 dispatch error tests.

## Test count growth

| Stage | Tests |
|---|---|
| Pre-G1-lift | 1528 |
| After G1-lift | **1552** (+24 G1 contracts + 11 runner + 6 transport + 7 stub-checks) |

## CI run

- PR #34 CI: 29705495637 ✅ pass (3.13 leg only — branched from main
  before G3 matrix landed). 1554 passed in 23.07s.
- Post-merge CI on main: 29705814369 ✅ both legs (3.12 + 3.13).
  Coverage 89.17% (gate 89% reached).

## Gotchas captured

- `coverage.py`'s `fail_under` uses the **combined statements+branches**
  metric, not statements alone. 91.67% statements + 84.10% branches =
  88.99% combined.
- The `TOTAL` column in coverage.py reports the rounded display value
  (89%) even when the precise combined value is 88.99 — fails the gate.
- `CommandResult` is a `@dataclass(frozen=True)`, not a Pydantic model —
  mutation raises `FrozenInstanceError`, not `ValidationError`.
- `StubRunner.run_task` writes `command.txt / stdout.txt / stderr.txt /
  result.json` (not `*.evidence.json`).
- There is a circular import between `seharness.controller` and
  `seharness.orchestrator`. To import `runner.py` directly, you must
  first import `seharness.controller.run_ledger` (which bypasses
  `controller/__init__.py`) and then `seharness.orchestrator`.
- `PullRequestCheck` (Pydantic) uses `state` + `conclusion` (not `passed`).
- `RequiredChecksView` uses `branch / head_sha / required / all_checks /
  mergeable_unknown` (no `pr_number`).

## Long-term goal

The SPEC value is 90%. Each subsequent slice that surfaces new error
paths should add tests and lift the floor by another 1%. Next lift
candidate (90%): add tests for `orchestrator/orchestrator.py` (currently
83%) and `validation/weakening.py` (78%).

G1b's per-PR diff-cover is the actual regression-prevention mechanism —
a PR that drops overall coverage fails CI even when the floor stays
where it is.
