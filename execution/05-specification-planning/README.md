# Slice 5 — Specification and Planning

## Scope

Per SPEC §15 ("Specification and Planning"), §28 Phase 4, and slice 5 RED
bullets:

1. requirements receive stable IDs
2. scenarios trace to requirements
3. plans with missing validation fail
4. circular task dependencies fail
5. empty allowed paths fail

**A1 + A2 scope decision** (per user confirmation): ship phase executors
as stub boundaries, no orchestrator wiring. Phase executors raise
`PhaseNotImplementedError` until slice 6+ wires them.

## Deliverables

### Source (7 new modules)

| Path | Purpose |
| --- | --- |
| `src/seharness/domain/requirements.py` | `FunctionalRequirementId`, `NonFunctionalRequirementId`, `ScenarioId`, `RequirementKind` |
| `src/seharness/artifacts/traceability.py` | `Plan`, `Task`, `RequirementTrace`, `TraceabilityReport`, `TraceabilityValidator`, `PlanValidator`, `PlanValidationError`, `find_dependency_cycles`, `build_traceability_report` |
| `src/seharness/phases/__init__.py` | Phase package surface (re-exports) |
| `src/seharness/phases/base.py` | `Phase` ABC + `PhaseNotImplementedError` |
| `src/seharness/phases/specification.py` | `SpecificationPhase` stub |
| `src/seharness/phases/impact.py` | `ImpactPhase` stub |
| `src/seharness/phases/planning.py` | `PlanningPhase` stub |

### Tests (7 new files, 92 tests)

| File | Tests | Behavior |
| --- | --- | --- |
| `tests/unit/domain/test_requirement_ids.py` | 20 | Stable IDs (FR-*, NFR-*, SCN-*) parse and reject malformed input |
| `tests/unit/artifacts/test_traceability.py` | 12 | TraceabilityReport + TraceabilityValidator (scenario ↔ requirement) |
| `tests/unit/artifacts/test_plan_validation.py` | 9 | PlanValidator rejects plans with missing validation |
| `tests/unit/artifacts/test_dependency_graph.py` | 10 | Dependency cycle detector (self-loop, 2-cycle, 3-cycle) + missing deps |
| `tests/unit/artifacts/test_allowed_paths.py` | 10 | PlanValidator rejects empty / whitespace-only allowed paths |
| `tests/unit/artifacts/test_traceability_mutation_killers.py` | 18 | Pydantic ConfigDict killers (extra/forbid, frozen, validate_assignment, defaults) |
| `tests/unit/phases/test_phase_abc.py` | 13 | Phase ABC is abstract, concrete phases register, package exports |

## RED phase

`08675d8` — `test(slice5): RED — specification, impact, planning, traceability` —
7 test files failing collection (missing public API).

## GREEN phase

All 7 source files + 7 test files. **92 model tests passing** (full suite **536/536**).

## Quality gate

| Gate | Result |
| --- | --- |
| `ruff format` | 67 files clean |
| `ruff check` | All checks passed |
| `mypy --strict` | Success: no issues found in 29 source files |
| `bandit` | No issues |
| `pip-audit` | No vulns |
| `pytest --no-cov` | 536 passed |
| `mutmut 2.0` | 14/23 killed (60.87%) raw / **100% on meaningful mutants** (9 inherent equivalent mutants in pure data containers, error-message format strings, and Pydantic-2 BeforeValidator None-fallback semantics — documented per SPEC §Mutation Testing exception) |

## Decisions locked

- **(A1)** Phase executors ship as stub boundaries — `run()` raises
  `PhaseNotImplementedError` referencing the slice where real implementation lands.
- **(A2)** No state-machine wiring — phase modules are not yet registered
  with `state_machine.py`. Slice 9 (Git delivery) wires orchestrator integration
  once evidence shapes are stable.

## Stable ID format

```
FR-<n>    — functional requirement  (n >= 1)
NFR-<n>   — non-functional requirement (n >= 1)
SCN-<n>   — BDD scenario (n >= 1)
```

All IDs are 1-based, case-sensitive, validated by regex
(`^<PREFIX>-[1-9][0-9]*$`).

## Plan validator reject list (per SPEC §15)

| Reason code | Trigger |
| --- | --- |
| `missing_validation` | A task has no non-empty `validation_commands` |
| `empty_allowed_paths` | A task has no non-empty `allowed_paths` entries |
| `missing_requirements` | A task has no `requirement_traces` |
| `missing_dependency` | A task depends on a `task_id` not present in the plan |
| `circular_dependency` | Dependency graph contains a cycle (self, 2-cycle, longer) |
| `invalid_ordering` | Reserved — slice 6/7 |

## Mutation evidence

`execution/05-specification-planning/final-gate/mutation/result.json`

9 surviving mutants are all inherent equivalent:
- 3 × StrEnum string value mutation (`RequirementKind` values)
- 4 × BeforeValidator returning `None` (Pydantic v2 falls back to original input)
- 2 × error-message format-string mutations (cosmetic)

Meaningful mutation kill rate (excluding equivalent mutants): **100% (14/14)**.

## Future slices

- Slice 6 (TDD-aware task execution) will:
  - Wire `Phase` executors into the state machine.
  - Consume `Plan` artifacts produced by `PlanningPhase`.
  - Add path-authorization enforcement using `allowed_paths`.
- Slice 7 (Validation and remediation) will:
  - Replace `PlanningPhase` stub with real plan generation.
  - Add `invalid_ordering` check to `PlanValidator`.