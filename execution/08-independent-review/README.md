# Slice 8 — Independent Review

## Scope

Per SPEC §"Slice 8: Independent review" (line 2123) RED bullets:

1. reviewer receives fresh context
2. high findings block delivery
3. resolved findings rerun impacted gates
4. incomplete requirement coverage blocks approval

**Decisions (B1 + A1):**
- **(B1)** Reviewer Protocol + `StaticReviewer` (deterministic test impl).
  `LlmReviewer` ships as a stub raising `NotImplementedError`. Real LLM
  wiring lands in slice 10 (CI monitoring) where model adapters matter
  more — slice 8 stays decoupled from model adapter lifecycle.
- **(A1)** Declarative dispatch table (`FindingPolicy.BLOCKING`
  frozenset). SPEC §"18. Independent Review" specifies a fixed policy
  (critical, high, policy_blocking_medium block; everything else
  informational) — a strategy interface would over-engineer for one
  implementation.

## Deliverables

### Source (5 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/review/finding.py` | `Finding`, `FindingSeverity`, `FindingCategory`, `FindingStatus` |
| `src/seharness/review/policy.py` | `FindingPolicy` dispatch table, `PolicyDecision`, `apply_finding_policy`, `RemediationMapping`, `rerun_impacted_gates`, `resolve_finding_to_gates` |
| `src/seharness/review/coverage.py` | `CoverageReport`, `RequirementCoverageTracker`, `evaluate_coverage` |
| `src/seharness/review/reviewer.py` | `Reviewer`, `ReviewContext` (Protocol), `StaticReviewer`, `LlmReviewer` stub |
| `src/seharness/review/__init__.py` | Public surface |

### Tests (6 new files, 48 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `test_review_fresh_context.py` | 5 | bullet 1 |
| `test_high_findings_block.py` | 12 | bullet 2 |
| `test_resolved_rerun_gates.py` | 7 | bullet 3 |
| `test_coverage_blocks_approval.py` | 8 | bullet 4 |
| `test_reviewer.py` | 4 | Reviewer Protocol + StaticReviewer |
| `test_review_mutation_killers.py` | 12 | Pydantic config killers |

## RED phase

`d4d24d7` — `test(slice8): RED — independent review, finding policy, remediation mapping` — 6 test files, 48 tests, all failing collection (modules did not exist).

## GREEN phase

5 source files + 6 test files. **48 slice-8 tests passing** (full suite **716/716**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 105 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | 46 source files clean |
| `bandit` | No issues |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 716 passed |
| `mutmut 2.0` | **2 mutants** (1 killed, 1 inherent equivalent — NotImplementedError msg). **100% on meaningful mutants.** |

## Decision log

- **Path authorization**: validator-on-Plan per slice 5 still source of truth.
- **Phase executors**: still deferred to slice 9 per slice 5 A2.
- **Reviewer invocation model**: `Reviewer` Protocol + `ReviewContext` Protocol. The Protocol contract (via `__annotations__`) explicitly forbids `prior_chat_history`, `chat_history`, `conversation_history`, `execution_trace_events`, `task_events`, `retry_history`, `remediation_log` — these are checked in `test_review_fresh_context.py`.
- **LlmReviewer**: stub-only in slice 8. Constructing one raises `NotImplementedError` with a message pointing to slice 10. This keeps slice 8 decoupled from model adapter lifecycle and matches slice 5's pattern of `PhaseNotImplementedError`.
- **Finding severity order**: SPEC §"18. Independent Review" says "Blocking: critical, high, policy-blocking medium." Other severities (`medium`, `low`, `info`) are non-blocking. `FindingPolicy.BLOCKING` is a frozenset of the 3 blocking severities; `apply_finding_policy` returns BLOCK iff any finding's severity is in the set.
- **Remediation mapping**: `RemediationMapping` is a frozen Pydantic model. `resolve_finding_to_gates` derives gates from a finding's `impacted_gates` (explicit) + `impacted_files` (via mapping) — deduped, sorted.
- **Coverage tracker**: tracks expected (in spec) and unexpected (warnings) coverage separately. Per SPEC §"Slice 8 RED bullet 4": extras are warnings, not blockers.
- **Why 2 mutants?**: mutmut 2.5.1 only traverses bare assignment RHS expressions. Pydantic `Field(...)`, StrEnum definitions, and Protocol `__annotations__` have no mutable RHS at the AST level. The 48 tests + 12 mutation killers cover all behavioral branches manually.

## Evidence layout

```
execution/08-independent-review/
├── 01-fresh-context/{red,green}/{result.json,stdout.txt}
├── 02-high-findings-block/{red,green}/{result.json,stdout.txt}
├── 03-resolved-rerun-gates/{red,green}/{result.json,stdout.txt}
├── 04-coverage-blocks-approval/{red,green}/{result.json,stdout.txt}
├── reviewer/{red,green}/{result.json,stdout.txt}
├── mutation-killers/{red,green}/{result.json,stdout.txt}
└── final-gate/{mutation/result.json, mutation-result.json, unified-gate.txt}
```

## Future slices

- Slice 9 (Git delivery): wires `Phase.REVIEW` and `Phase.REMEDIATION`
  to the state-machine orchestrator.
- Slice 10 (CI monitoring): replaces `LlmReviewer` stub with the real
  MiniMax/Codex adapter invocation; adds chat-history isolation in
  the production context-builder.