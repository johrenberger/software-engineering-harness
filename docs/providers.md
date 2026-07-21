# Providers and credentials

This document describes how the software-engineering-harness
selects, configures, and authenticates against model providers.

> **Status:** v0.2 — provider / credential config (the env-var
> contract is shipped; the multi-provider config file format
> `config/providers.toml` is not yet finalized). This document
> describes **what works today** and what is intentionally not
> yet wired.

## TL;DR

| Where | Today | Status |
|---|---|---|
| **Provider selection** | `harness.yaml` → `models:` block (committed) or `seharness.local.yaml` (git-ignored) | ✅ works |
| **Provider credentials** | none required today — adapters fail closed | ✅ correct (no creds to leak) |
| **`config/providers.toml` file** | not used; only `harness.yaml` is consulted | ⚠️ not yet |
| **Env-var credential loading** (`SEHARNESS_PROVIDER_*_API_KEY`) | not wired | ⚠️ not yet |
| **Live MiniMax HTTP client** | **DONE** (cluster N) — `HttpMiniMaxTransport` against `https://api.minimax.io/v1` (default OpenAI-compatible) + `MODELS_ENDPOINT` for `validate_model_against_account(...)`; bearer token read from env at call time; `__repr__` excludes endpoint; test-only `RecordingMiniMaxTransport` for offline replay | `src/seharness/models/minimax_transport.py` |
| **Live Codex subprocess transport** | boundary class registered, `invoke()` fails closed | ⚠️ not yet |
| **`delivery` routing slot** | default is `minimax` (per `router.py`); not in the example YAML above | ⚠️ partial |

## Available providers

Per `src/seharness/config.py::_KNOWN_PROVIDERS`, exactly two provider
identifiers are recognized today:

| Provider ID | Kind | Purpose |
|---|---|---|
| `minimax` | `LIVE` (HTTP) | Default planning + review (per SPEC §10). |
| `codex` | `LOCAL` (subprocess) | Default implementation + remediation. |

`MiniMaxAdapter` (`src/seharness/models/minimax.py`) is now backed by
the real `HttpMiniMaxTransport` (`src/seharness/models/minimax_transport.py`).
The transport protocol is runtime-checkable; `FakeMiniMaxTransport`
plus `RecordingMiniMaxTransport` round out the cluster N fixture
suite. Bearer tokens are read from env at call time, never stored
as fields; `__repr__` excludes endpoint URLs.

`CodexAdapter` (`src/seharness/models/codex.py`) still fails closed
in `invoke()`; the cluster-N work did not modify it.

Capability-based readiness replaces class-name detection:
`MiniMaxAdapter.readiness()` (`src/seharness/models/provider_readiness.py`)
returns a structured `ProviderReadiness` struct evaluated at
construction. Production wiring calls
`validate_router_readiness(...)` (`src/seharness/models/readiness_validation.py`)
to fail-closed when an adapter reports not-live. See the architecture
overview for the full model-layer layering.

## Configuring routing (works today)

Provider routing is configured in the `models:` block of
`harness.yaml` (committed, shared across the team) or
`seharness.local.yaml` (git-ignored, machine-specific overrides).

### Default routing (SPEC §10)

```yaml
models:
  planning: minimax
  implementation: codex
  remediation: codex
  review: minimax
  delivery: minimax
  fallback:
    minimax: codex
    codex: minimax
```

Each routing role accepts exactly one of the two provider IDs.
Unknown provider identifiers are rejected at validation time (the
config loader raises `ValueError("unknown model provider: ...")`
before any run starts — see
`ModelRouting.validate_provider`).

### Per-machine override

To override the routing on your local checkout without committing:

```bash
cat > seharness.local.yaml <<'YAML'
models:
  planning: codex       # use Codex for planning locally
YAML
```

Configuration precedence (highest wins):

1. CLI overrides
2. Environment variables (`SEHARNESS_MODELS__PLANNING=codex`)
3. `seharness.local.yaml` (git-ignored)
4. `harness.yaml` (committed)
5. Built-in defaults

Never put credentials in `harness.yaml` — it is committed to the
repository and visible to anyone with read access. See the
"Credential loading" section below.

## Fallback behavior

The `fallback:` sub-table declares the alternate provider for each
primary. When the primary adapter returns one of the canonical
failure kinds (`provider_failure`, `timeout`, `malformed_output`
after one repair attempt), the router switches to the configured
fallback. See `src/seharness/models/router.py` for the full logic.

A few important caveats:

- **Do NOT fall back on `auth` errors.** An auth failure means the
  credential is wrong, and switching providers does not fix that.
  The router surfaces `auth` errors to the caller unchanged.
- **One repair attempt, then fall back.** The router tries
  `StructuredOutputRepair.maybe_repair` once before invoking the
  fallback. If the repair succeeds, the repaired response is
  returned; if not, the fallback provider is invoked with the
  original request.
- **No retries within the same provider.** Per SPEC §10, the router
  does not retry the primary on its own retry budget. That is the
  caller's responsibility (see `ExecutionConfig.task_retry_limit`).

## Credential loading (not yet wired)

> **Honesty:** As of v0.2, **there are no environment variables for
> provider credentials**. The adapter boundaries fail closed, so no
> real credentials are loaded anywhere in the codebase. The
> `config/providers.toml` file format referenced in the README's
> honesty matrix does not exist. Adding credential loading is a
> follow-up track (see the README's "What's partial" table).

When the real transports land, the planned credential-loading
flow is:

1. Look for `SEHARNESS_PROVIDER_<PROVIDER>_API_KEY` (e.g.
   `SEHARNESS_PROVIDER_MINIMAX_API_KEY`).
2. Else, look in `config/providers.toml` under
   `[providers.<provider>].api_key`.
3. Else, fail closed with a normalized `auth` error.

`config/providers.toml` will be added to `.gitignore` so credentials
stay out of the repo. Until then, **do not** add an `api_key` field
to `harness.yaml` — there is no code path that reads it, and a
committed credential is a leak waiting to happen.

## Testing provider routing

Most provider tests use `FakeModelAdapter` (deterministic,
fixture-driven). The router's behavior is pinned by
`tests/unit/models/test_model_contract.py` and
`tests/unit/models/test_models_mutation_killers.py`. To exercise
the real routing without a real network:

```python
from seharness.models import (
    FakeModelAdapter,
    ModelRouter,
    ProviderName,
)
from seharness.domain.enums import RoutingRole

router = ModelRouter(
    adapters={
        ProviderName.MINIMAX: FakeModelAdapter(...),
        ProviderName.CODEX: FakeModelAdapter(...),
    }
)
response = router.invoke(some_request)
```

The mutation-killers test suite ensures that no PR can disable the
fallback logic or the structured-output repair without breaking CI.

## See also

- `docs/architecture-overview.md` — model-layer architecture.
- `docs/user/configure.md` — env-var configuration for the rest of
  the harness (Telegram, GitHub, dashboard).
- `src/seharness/config.py` — full `HarnessConfig` schema.
- `src/seharness/models/router.py` — `ModelRouter` implementation.
- `src/seharness/models/base.py` — `ModelAdapter` contract.
- README's "What's partial" table — the user-facing honesty
  statement.
