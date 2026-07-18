# Slice 4 — Model Contract and Fake Adapter

## Scope

Per SPEC §10 and §28 Phase 3, Slice 4 ships the **boundaries** of the model
adapter layer. Real adapter implementations (HTTP-shape stubs) for MiniMax
and Codex are stubbed in this slice and deferred to slices 6/8.

## Deliverables

### Source (8 new files / 2 extensions)

| Path | Purpose |
| --- | --- |
| `src/seharness/models/__init__.py` | Public API surface, registry (`register_adapter` / `get_adapter`) |
| `src/seharness/models/base.py` | `ModelAdapter` ABC + `ModelAdapterError` |
| `src/seharness/models/fake.py` | `FakeModelAdapter` — fixture-driven, deterministic, all failure modes |
| `src/seharness/models/router.py` | `ModelRouter` — role → provider table, fallback chain |
| `src/seharness/models/output_repair.py` | `StructuredOutputRepair` — one-shot repair policy |
| `src/seharness/models/minimax.py` | `MiniMaxAdapter` — fails closed (HTTP-shape stub) |
| `src/seharness/models/codex.py` | `CodexAdapter` — fails closed (HTTP-shape stub) |
| `src/seharness/domain/requests.py` | `ModelRequest` (provider-neutral) |
| `src/seharness/domain/results.py` | `ModelUsage`, `ModelError`, `ModelResponse`, `ModelRepair` |
| `src/seharness/domain/enums.py` (extend) | Promote `ProviderName` to StrEnum + add `ProviderKind`, `RoutingRole`, `RepairOutcome` |
| `src/seharness/config.py` (re-export) | Re-export `ProviderName` from domain (slice 1 backward-compat) |

### Tests (92 new tests, 6 files)

| File | Tests | Behavior |
| --- | --- | --- |
| `tests/unit/models/test_model_contract.py` | 20 | Identical provider-neutral request reaches all adapters (ABC + registry) |
| `tests/unit/models/test_fake_adapter.py` | 12 | Fake adapter simulates all failure modes (timeout, malformed, provider error, controlled source change) |
| `tests/unit/models/test_router.py` | 7 | Router routes by role with fallback on provider failure; never switches on validation defects |
| `tests/unit/models/test_output_repair.py` | 7 | Malformed structured output triggers exactly ONE repair attempt |
| `tests/unit/models/test_normalized_failures.py` | 13 | Timeout / provider-error → `ModelError` with retryable flag |
| `tests/unit/models/test_models_mutation_killers.py` | 33 | Pydantic config killers: extra/forbid, frozen, validate_assignment, default Field() bounds |

## RED phase

`b7e209a` — `test(models): RED — slice 4 (model contract, fake adapter,
router, output repair, normalized failures)` — 5 behaviours failing collection.

## GREEN phase

Implemented all 8 source files + 6 test files. Final test count:
**92 model tests passing** (full suite **444/444**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 53 files unchanged |
| `ruff check` | All checks passed |
| `mypy --strict` | Success: no issues found in 22 source files |
| `bandit` | No issues |
| `pip-audit` | No vulnerabilities |
| `pytest --no-cov` | 444 passed |
| `mutmut 2.0` | 40/46 killed (86.96%); 6 inherent equivalent mutants (ConfigDict kwarg renames) |

## Decisions locked

- **(B) Real adapter boundaries ship with HTTP-shape stubs that fail closed.**
  `MiniMaxAdapter` and `CodexAdapter` raise `ModelAdapterError("not yet wired;
  slice 6/8")` so an unconfigured run fails loudly rather than silently returning
  empty output.
- **Promote `ProviderName` to enum** in `domain/enums.py`. The slice-1
  `Literal["minimax", "codex"]` alias at `src/seharness/config.py:24` is
  preserved as a re-export so slice 1 tests stay green.

## Default routing

```
PLANNING       → minimax
IMPLEMENTATION → codex
REMEDIATION    → codex
REVIEW         → minimax
DELIVERY       → minimax
Fallback chain: minimax ↔ codex
```

## Repair policy

Exactly ONE repair attempt on malformed structured output. After a single
failed attempt the response is rejected and the router decides whether to
fall back to another provider.

## Mutation evidence

`execution/04-model-contracts/final-gate/mutation/result.json`

6 surviving mutants are all `ConfigDict(extra=..., frozen=..., validate_assignment=...)`
keyword renames (`extraXX`, `frozenXX`, `validate_assignmentXX`). Pydantic
`ConfigDict` silently ignores unknown keys, so these are inherent equivalent
mutations per SPEC §"Mandatory Mutation Testing" exception.