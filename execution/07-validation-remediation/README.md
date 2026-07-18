# Slice 7 — Validation and Remediation

## Scope

Per SPEC §28 Phase 4 (slice 7) RED bullets:

1. failed commands create normalized failures
2. remediation receives only bounded evidence
3. regression defects require a failing test first
4. exhausted retries fail the run
5. weakening tests is detected

**Decisions (A1 + A2):**
- **(A1)** Per-task retry budget (`RetryBudget` / `RetryBudgetRegistry`).
  Run-level bookkeeping deferred (not needed for slice 7).
- **(A2)** Test weakening via body diff vs previous GREEN. No
  coverage tooling.

## Deliverables

### Source (6 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/validation/runner.py` | `CommandResult`, `FailureKind`, `NormalizedFailure`, `ValidationRunner`, `SubprocessRunner` |
| `src/seharness/validation/classifier.py` | `FailureClassifier`, `ClassificationError` |
| `src/seharness/validation/retry.py` | `RetryBudget`, `RetryBudgetRegistry`, `RetriesExhausted` |
| `src/seharness/validation/weakening.py` | `TestWeakeningDetector`, `Weakening`, `WeakeningKind` |
| `src/seharness/validation/remediation.py` | `BoundedEvidence`, `BoundedEvidenceBuilder`, `RemediationController`, `RemediationResult`, errors |
| `src/seharness/validation/__init__.py` | Public surface |

### Tests (7 new files, 68 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_failures_normalized.py` | 14 | `CommandResult` + `NormalizedFailure` + `FailureClassifier` |
| `test_bounded_evidence.py` | 5 | `BoundedEvidence` envelope (no full-repo leak) |
| `test_regression_test_first.py` | 6 | `RegressionTestRequired` + `RegressionTestNotFailing` |
| `test_retries_exhausted.py` | 12 | `RetryBudget` + `RetryBudgetRegistry` |
| `test_weakening_tests.py` | 9 | `TestWeakeningDetector` + `WeakeningKind` |
| `test_remediation_controller.py` | 4 | `RemediationController` public boundary |
| `test_validation_mutation_killers.py` | 11 | Pydantic config killers |
| `test_validation_runner.py` | 7 | `ValidationRunner` protocol + `SubprocessRunner` |

## RED phase

`38db253` — `test(slice7): RED — validation runner, classifier, remediation, retry budgets` —
7 test files, 68 tests, all failing collection (modules did not exist).

## GREEN phase

6 source files + 7 test files. **68 model tests passing** (full suite **668/668**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 94 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 41 source files clean |
| `bandit` | No issues |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 668 passed |
| `mutmut 2.0` | **21/32 killed (65.62%)** raw / **84% on meaningful mutants** (7 inherent equivalent + 4 slow-equivalent timeouts) |

## Decision log

- **Path authorization**: validator-on-Plan per slice 5 is source of
  truth. `revert_unauthorized` is defense-in-depth (slice 6). Slice 7
  doesn't add new auth surface.
- **Phase executors**: still deferred to slice 9 per slice 5 A2.
- **Runner contract**: `ValidationRunner` Protocol + `SubprocessRunner`
  default. Slice 6's `TaskExecutionService` injects its own runner
  (slice 6 was deliberately agnostic to validation; slice 7 plugs the
  gap).
- **Retry budget**: per-task `RetryBudget` keyed by `task_id`. The
  controller calls `record_attempt()` for each real attempt; the
  validation probe does NOT consume the budget.
- **BoundedEvidence**: caps `max_bytes_per_file` (default 4 KB) and
  `max_total_bytes` (default 32 KB). Filters to `allowed_paths`.
  `previous_green` is `None` in slice 7 — slice 9 may populate it from
  state-machine history.
- **Weakening detector**: line-level heuristic. Detects 5 patterns:
  DELETED_ASSERTION, SKIP_REPLACES_ASSERTION, TRIVIAL_ASSERTION,
  EMPTY_TEST_BODY, WIDENED_EXCEPTION.

## Evidence layout

```
execution/07-validation-remediation/
├── 01-failures-normalized/{red,green}/{result.json,stdout.txt}
├── 02-bounded-evidence/{red,green}/{result.json,stdout.txt}
├── 03-regression-test-first/{red,green}/{result.json,stdout.txt}
├── 04-retries-exhausted/{red,green}/{result.json,stdout.txt}
├── 05-weakening-tests/{red,green}/{result.json,stdout.txt}
├── remediation/{red,green}/{result.json,stdout.txt}
├── mutation-killers/{red,green}/{result.json,stdout.txt}
├── runner/{red,green}/{result.json,stdout.txt}
└── final-gate/{mutation/result.json, mutation-result.json, unified-gate.txt}
```

## Future slices

- Slice 8: closed loop and observability (likely adds coverage delta
  detection to validation, replacing the heuristic weakening detector
  with AST-based analysis).
- Slice 9 (Git delivery): wires `TaskExecutionService` + `RemediationController`
  + retry budgets into the state-machine orchestrator.