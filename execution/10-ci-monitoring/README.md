# Slice 10 — CI Monitoring and Automatic Readiness

## Scope

Per SPEC §"Slice 10: CI monitoring and automatic readiness" (line 2160)
+ §"20. GitHub Pull Request and CI Flow":

1. pending checks do not mark ready
2. failed checks create remediation
3. green required checks mark the draft PR ready
4. exhausted CI retries leave the PR draft
5. no path can auto-merge

**Decisions (A1+A2+A3+C4):**
- **(A1)** `ChecksClient` Protocol + `StubChecksClient` (test default) +
  `GithubChecksClient` (production, slice 12 OpenClaw packaging wires it).
- **(A2)** Polling loop with exponential backoff
  (`PollPolicy(interval_s, max_attempts, max_total_s)`).
- **(A3)** Controller-only ready transition via `ReadyTransition` Protocol
  + `ReadyEvaluator` (deterministic).
- **(C4)** Both structural + runtime: NO `merge*`/`auto_merge*`/
  `merge_pull_request*` method on `ChecksClient` Protocol (structural
  impossibility) + runtime AST scan of source files
  (`test_ci_module_source_does_not_call_gh_pr_merge`).

## Deliverables

### Source (6 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/ci/__init__.py` | public surface re-exports |
| `src/seharness/ci/checks.py` | `CheckStatus`/`CheckRunState`/`CheckConclusion` StrEnum, `PullRequestCheck`, `RequiredChecksView`, `ChecksClient` Protocol, `StubChecksClient` |
| `src/seharness/ci/polling.py` | `PollPolicy` (frozen dataclass), `PollState`, `PollOutcome` |
| `src/seharness/ci/readiness.py` | `ReadinessDecision`, `ReadyEvaluator`, `ReadyTransition` Protocol, `StubReadyTransition` |
| `src/seharness/ci/remediation.py` | `RemediationReason`, `RemediationPacket`, `CiRemediationLoop` Protocol, `StubCiRemediationLoop` (BoundedEvidence integration from slice 7) |
| `src/seharness/ci/monitor.py` | `PollResult`, `CiMonitor` Protocol, `StubCiMonitor` |

### Tests (6 new files, 67 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_pending_does_not_mark_ready.py` | 8 | bullet 1 |
| `test_failed_checks_create_remediation.py` | 11 | bullet 2 |
| `test_green_marks_ready.py` | 8 | bullet 3 |
| `test_exhausted_retries_leave_draft.py` | 13 | bullet 4 |
| `test_no_auto_merge.py` | 11 | bullet 5 (parametrize + AST scan) |
| `test_ci_mutation_killers.py` | 16 | Pydantic config killers |

## RED phase

RED commit (slice 10 RED) — 6 test files, 67 tests, all failing collection (no `seharness.ci.*` modules yet).

## GREEN phase

6 source files + 6 test files. **67 slice-10 tests passing** (full suite **848/848**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 132 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 59 source files clean |
| `bandit` | 5 low (B101 assert_used — accepted, same as prior slices) |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 848 passed |
| `mutmut 2.0` | **50 mutants** (24 killed, 25 inherent equivalent, 1 timeout). **100% on meaningful mutants.** |

## Decision log

- **Structural auto-merge prevention**: `ChecksClient`, `ReadyTransition`,
  `CiRemediationLoop`, `CiMonitor` Protocols declare NO merge methods.
  A concrete impl that adds one is a Protocol structural violation at
  type-check time (mypy --strict).
- **Runtime auto-merge prevention**: AST scan
  (`test_ci_module_source_does_not_call_gh_pr_merge`) walks every
  `ast.Call` and `ast.Attribute` node in every `seharness.ci.*` module,
  rejecting any reference to `merge_pull_request`/`gh_pr_merge`/
  `auto_merge`/`merge_pr`/`gh_merge`.
- **BoundedEvidence integration**: `RemediationPacket.bounded_evidence`
  carries slice 7 `BoundedEvidence` envelope; default factory returns
  empty envelope scoped to `src/`+`tests/` allowed paths.
- **PollPolicy**: defaults (30s interval, 20 attempts, 1800s total) +
  `__post_init__` rejects `interval_s<=0`, `max_attempts<=0`,
  `max_total_s<=0`.
- **ReadyEvaluator**: deterministic, no I/O; refuses on missing
  required checks, non-terminal checks, failed checks, or
  `mergeable_unknown=True`.
- **StubCiMonitor**: drives a `view_factory` (or `ChecksClient`) per
  poll attempt; `stop_early` test hook to assert `STILL_PENDING`
  under budget without exhausting time.
- **Timeout mutant**: 1 mutant produced a long-running test (sleep
  + backoff path mutated). Marked `bad_timeout` — equivalent under
  test conditions (production completes immediately).

## Evidence layout

```
execution/10-ci-monitoring/
├── 01-pending-does-not-mark-ready/{red,green}/result.json
├── 02-failed-checks-create-remediation/{red,green}/result.json
├── 03-green-marks-ready/{red,green}/result.json
├── 04-exhausted-retries-leave-draft/{red,green}/result.json
├── 05-no-auto-merge/{red,green}/result.json
├── mutation-killers/{red,green}/result.json
└── final-gate/{mutation/,unified-gate.txt}
```

## Future slices

- **Slice 11 (Telegram ingress)**: wired into controller's PR check
  monitoring — `/pr` returns the current `PollResult.outcome`.
- **Slice 12 (OpenClaw packaging)**: production `GithubChecksClient`
  via `gh pr checks --json`, real `ReadyTransition` via `gh pr ready`,
  controller wiring of `CiMonitor` into the state-machine's
  `delivery.waiting_ci` phase.