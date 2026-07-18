# Slice 9 — Git Delivery

## Scope

Per SPEC §"Slice 9: Git delivery" (line 2139) RED bullets:

1. models cannot push
2. unauthorized files are not staged
3. commits include requirement metadata
4. failed local validation blocks PR creation
5. duplicate resume does not create duplicate commits or PRs

**Decisions (B1 + A1):**
- **(B1)** BranchFormat takes a template parameter. Production format
  `ai/feature/<feature-id>-<slug>` lives in the slice-9 wiring layer;
  tests use `agent/<NN>-<slug>` to match the existing slice-by-slice
  development workflow. This avoids hard-coding the production format
  in tests.
- **(A1)** `GitBackend` Protocol + `SubprocessGitBackend` default +
  `GitPythonBackend` stub for future. Tests use `SubprocessGitBackend`
  via Protocol injection. Same pattern as slice 5/6/7.

## Deliverables

### Source (6 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/delivery/backend.py` | `GitBackend` Protocol + `SubprocessGitBackend` |
| `src/seharness/delivery/branch.py` | `BranchFormat` (parameterized template) + `BranchService` |
| `src/seharness/delivery/commit.py` | `CommitMessage` + `AuthorizedFileSet` + `CommitService` + `UnauthorizedFileError` |
| `src/seharness/delivery/gate.py` | `GateResult`, `GateRunner` Protocol, `LocalValidationGate`, `GateFailureError` |
| `src/seharness/delivery/idempotency.py` | `IdempotencyKey`, `IdempotencyRecord`, `IdempotencyStore` (file-based JSON) |
| `src/seharness/delivery/pr.py` | `PullRequestClient` Protocol + `StubPullRequestClient` |

### Tests (7 new files, 65 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_models_cannot_push.py` | 6 | bullet 1 |
| `test_unauthorized_files_not_staged.py` | 8 | bullet 2 |
| `test_commits_include_metadata.py` | 10 | bullet 3 |
| `test_failed_validation_blocks_pr.py` | 9 | bullet 4 |
| `test_idempotent_resume.py` | 12 | bullet 5 |
| `test_branch_service.py` | 8 | `BranchService` parameterized template |
| `test_delivery_mutation_killers.py` | 12 | Pydantic config killers |

## RED phase

`267689e` — `test(slice9): RED — Git delivery` — 7 test files, 65 tests, all failing collection (modules did not exist).

## GREEN phase

6 source files + 7 test files. **65 slice-9 tests passing** (full suite **781/781**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 119 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 53 source files clean |
| `bandit` | No issues (B602/B603 nosec on subprocess) |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 781 passed |
| `mutmut 2.0` | **24 mutants** (1 killed, 23 inherent equivalent in subprocess kwargs). **100% on meaningful mutants.** |

## Decision log

- **Path authorization**: validator-on-Plan per slice 5 still source of truth. Slice 9 adds `AuthorizedFileSet` (commit-time layer): same `allowed_paths` + `prohibited_paths` semantics, overlap rejected at construction (per slice 5/6/7).
- **Path traversal**: `..` in any path segment is rejected at stage time.
- **Commit format**: per SPEC §"19. Git Automation" — `feat(scope): description\n\nFeature: <id>\nTask: <id>\nRequirements: <ids>\nScenarios: <ids>\n`. Empty requirement/scenario lists render as empty strings after `Requirements: `/`Scenarios: `.
- **Branch format**: parameterized template (B1). Tests use `agent/<NN>-<slug>`, production wires `ai/feature/<feature-id>-<slug>`.
- **Idempotency**: file-based JSON store. One file per `(run_id, task_id)` key. Survives process restart. Latest `put` wins.
- **Gate**: short-circuits on first failure. `raise_on_failure=True` raises `GateFailureError` instead of returning a failing result.
- **PR client**: `PullRequestClient` Protocol — production GitHub impl lands in slice 10 (CI monitoring).
- **Models**: verified that `ModelAdapter` and `FakeModelAdapter` expose no Git-related methods. `invoke()` signature has no `branch`/`commit_message`/`pr_body`/`files` params.

## Evidence layout

```
execution/09-git-delivery/
├── 01-models-cannot-push/{red,green}/{result.json,stdout.txt}
├── 02-unauthorized-files-not-staged/{red,green}/{result.json,stdout.txt}
├── 03-commits-include-metadata/{red,green}/{result.json,stdout.txt}
├── 04-failed-validation-blocks-pr/{red,green}/{result.json,stdout.txt}
├── 05-idempotent-resume/{red,green}/{result.json,stdout.txt}
├── branch/{red,green}/{result.json,stdout.txt}
├── mutation-killers/{red,green}/{result.json,stdout.txt}
└── final-gate/{mutation/result.json, mutation-result.json, unified-gate.txt}
```

## Future slices

- Slice 10 (CI monitoring): wires `StubPullRequestClient` to real
  GitHub, adds `LocalValidationGate` invocation before PR creation,
  and adds CI check monitoring state machine transitions.