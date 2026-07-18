# Slice 3 — Repository Discovery

**Branch:** `agent/03-repository-discovery`
**Base:** `main`
**Status:** GREEN (PR draft pending)
**Scope:** Framework-neutral Python repository discovery, command resolution, and baseline metadata recording. No subprocess execution — slice 7 owns the validation runner.

## Behaviors

| ID | Behavior | Tests |
|----|----------|-------|
| 01 | Repository profile (Pydantic schema) | 19 |
| 02 | Framework-neutral discovery (package manager, source/test roots, framework markers) | 20 |
| 03 | Command resolver (built-in gates + plugin `register()` contract) | 13 |
| 04 | Baseline recorder (atomic JSON snapshots + aggregate status) | 10 |
| 05 | Mutation killers (adversarial tests targeting surviving mutants) | 51 |
| **Total** | | **113** |

## Source modules

- `src/seharness/repository/__init__.py` — public API surface
- `src/seharness/repository/discovery.py` — `inspect_repository()`, `RepositoryProfile`, `PackageManager`, `FrameworkIndicator`, `BaselineStatus`, `BaselineSnapshot`, `ValidationCommand`, `RepositoryError`
- `src/seharness/repository/conventions.py` — `CommandResolver`, `Gate`, `BaselineRecorder`

## Evidence

```
execution/03-repository-discovery/
├── checkpoint.yaml
├── 01-repository-profile/
│   ├── red/{result.json, stdout.txt}
│   └── green/{result.json, stdout.txt}
├── 02-framework-neutral-discovery/
│   ├── red/{result.json, stdout.txt}
│   └── green/{result.json, stdout.txt}
├── 03-command-resolver/
│   ├── red/{result.json, stdout.txt}
│   └── green/{result.json, stdout.txt}
├── 04-baseline-recorder/
│   ├── red/{result.json, stdout.txt}
│   └── green/{result.json, stdout.txt}
└── final-gate/
    ├── unified-gate.txt
    └── mutation/{result.json}
```

## Final quality gate

- ruff format — pass (38 files clean)
- ruff check — pass (no issues)
- mypy --strict — pass (13 source files clean)
- bandit -r src/seharness — pass
- pip-audit — pass (no vulnerabilities)
- pytest --no-cov — pass (352 / 352 tests, full suite)
- mutmut 2.x — 62 / 66 (93.94 %) killed on `repository/discovery.py` + `repository/conventions.py`

### Mutation survivors (4, all equivalent)

1. `_CommandFactory = Callable[[RepositoryProfile], tuple[str, ...]]` — type alias, no runtime effect.
2. `raise ValueError(f"cannot replace built-in gate {gate!r}; use a different name")` — `{gate!r}` ↔ `{gate}` produces different strings for non-ASCII gate names, but the spec constrains gate names to ASCII identifiers, so the mutation is functionally equivalent.
3. `continue` removal in the resolve() loop — equivalent because the next branch's `factory is not None` short-circuits identically.
4. `raise ValueError(f"unknown gate: {gate!r}")` — same `{gate!r}` ↔ `{gate}` equivalent mutation.

These are inherent equivalent mutants; no behavioural test can kill them without violating the gate-name contract.
