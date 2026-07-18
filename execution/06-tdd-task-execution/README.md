# Slice 6 — TDD-aware task execution

## Scope

Per SPEC §28 Phase 4 (slice 6) RED bullets:

1. a task cannot complete without RED evidence
2. RED must fail for the expected reason
3. production changes before RED are rejected
4. GREEN must pass
5. unauthorized paths are reverted

**Decisions (A1 + B2):**
- **(A1)** Path authorization is the plan validator's job (per SPEC §15
  + slice 5 `PlanValidator`). The runtime guard `revert_unauthorized`
  exists for defense in depth but is not the primary enforcement layer.
- **(B2)** Phase executors are NOT wired into the state machine this slice.
  Per slice 5 A2 decision, phase integration lands in slice 9.

## Deliverables

### Source (6 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/execution/evidence.py` | `RedResult`, `GreenResult`, `FailureKind`, `TaskEvidenceLayout` |
| `src/seharness/execution/paths.py` | `AllowedPaths`, `ProhibitedPaths`, `PathAuthorizationRule` |
| `src/seharness/execution/workspace.py` | `WorkspaceSnapshot`, `PathClassifier`, `PreRedViolation`, `detect_pre_red_violations`, `revert_unauthorized` |
| `src/seharness/execution/completion.py` | `TaskCompletionValidator`, `CompletionRejection` |
| `src/seharness/execution/service.py` | `TaskExecutionService`, `TaskResult`, `TaskEvidenceError`, `TaskNotFoundError` |
| `src/seharness/execution/__init__.py` | Public surface |

### Tests (7 new files, 64 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_red_evidence_required.py` | 10 | RED directory + file completeness + failure_kind requirement |
| `test_red_must_fail.py` | 8 | RED must fail with `expected_failure` (not collection / infrastructure) |
| `test_production_before_red.py` | 8 | `PathClassifier` + pre-RED production change detection |
| `test_green_required.py` | 9 | GREEN must pass + regression coverage enforcement |
| `test_path_controls.py` | 11 | `PathAuthorizationRule` + `revert_unauthorized` |
| `test_task_execution_service.py` | 5 | `TaskExecutionService` boundary |
| `test_execution_mutation_killers.py` | 13 | Pydantic config killers for slice-6 models |

## RED phase

`6e95a45` — `test(slice6): RED — TDD-aware task execution, path controls` —
7 test files, all failing collection (modules did not exist).

## GREEN phase

6 source files + 7 test files. **64 model tests passing** (full suite **600/600**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 80 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 35 source files clean |
| `bandit` | No issues |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 600 passed |
| `mutmut 2.0` | **24/52 killed (46.15%)** raw / **100% on meaningful mutants** (28 inherent equivalent mutants documented) |

## Decision log

- **Path authorization**: validator-on-Plan per slice 5 is the source of
  truth. Slice 6 adds the runtime `revert_unauthorized` for defence in
  depth (catches hand-edited artifacts). NOT the primary gate.
- **Phase wiring**: deferred to slice 9 per slice 5 A2. Phase ABC from
  slice 5 stays a stub.
- **Task schema**: slice 5's `Task` lacks `prohibited_paths` (per the
  slice 5 docstring it was reserved for slice 6). For now the service
  passes an empty prohibition list; slice 6 does NOT add the field here
  to keep the slice boundary clean.
- **Failure kind enum**: closed `StrEnum` with 4 documented buckets
  (`EXPECTED_FAILURE`, `UNRELATED_FAILURE`, `COLLECTION_ERROR`,
  `INFRASTRUCTURE_ERROR`). Anything else is rejected.

## Evidence layout

```
execution/06-tdd-task-execution/
├── 01-red-required/{red,green}/{result.json,stdout.txt}
├── 02-red-must-fail/{red,green}/{result.json,stdout.txt}
├── 03-prod-before-red/{red,green}/{result.json,stdout.txt}
├── 04-green-required/{red,green}/{result.json,stdout.txt}
├── 05-path-controls/{red,green}/{result.json,stdout.txt}
├── service/{red,green}/{result.json,stdout.txt}
├── mutation-killers/{red,green}/{result.json,stdout.txt}
└── final-gate/{mutation/result.json, mutation-result.json, unified-gate.txt}
```

## Future slices

- Slice 7 (Validation and remediation) will:
  - Add a concrete `PytestRunner` that satisfies the `Runner` protocol.
  - Add the `invalid_ordering` reject reason to `PlanValidator`.
  - Add retry-budget logic to `TaskExecutionService`.
- Slice 9 (Git delivery) will:
  - Wire phase executors to `state_machine.py` transitions.
  - Use `TaskExecutionService` as the task executor.