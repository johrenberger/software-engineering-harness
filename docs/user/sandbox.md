# Sandbox (Cluster C)

The sandbox layer is what stands between agent-generated code and the rest
of the host. Path authorization prevents writes outside the repository; the
sandbox prevents reads of secrets, network exfiltration, fork bombs, and
wall-clock runaway.

This document covers:

1. [Threat model](#threat-model)
2. [`SandboxProfile` schema](#sandboxprofile-schema)
3. [Executors](#executors)
4. [Wire-in points](#wire-in-points)
5. [Examples](#examples)
6. [Performance overhead](#performance-overhead)
7. [Testing notes](#testing-notes)
8. [Known limits and follow-ups](#known-limits)

---

## Threat model

Cluster C addresses the *execution* part of the agent threat model. The
goal is to ensure that agent-generated code, validation commands, and
review scripts cannot escape the boundaries the operator configured.

| In scope | Out of scope (handled elsewhere) |
|---|---|
| Reads of files outside `allowed_paths` | Writes outside `repo_root` — handled by path authorization |
| Network egress to non-allowlisted destinations | GitHub API authorization — handled by `GitHubChecksClient` |
| Env-var exfiltration of secrets | Prompt-injection in repo files — handled by Cluster E (audit) and Cluster F (context) |
| CPU / memory / disk / fork bombs | Container breakout at the kernel level — relies on Docker defaults |
| Wall-clock runaway | Side-channel CPU timing — accepted residual risk |

The defaults are **fail-closed**: empty `allowed_paths`, empty
`allowed_network_destinations`, and a non-empty `denied_env_vars` list
that always scrubs `PATH`, `HOME`, `AWS_*`, `GITHUB_TOKEN`, `GH_TOKEN`,
`GITLAB_TOKEN`, `NPM_TOKEN`, `PYPI_TOKEN`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENCLAW_TOKEN`. Anything operator-added via
`denied_env_vars=...` is **appended**, never replacing the defaults.

---

## `SandboxProfile` schema

```python
from seharness.sandbox import SandboxProfile

profile = SandboxProfile(
    cwd="/path/to/agent/cwd",
    allowed_paths=("/path/to/agent/cwd",),
    allowed_network_destinations=("pypi.org",),  # hostname, IPv4, or CIDR
    denied_env_vars=("MY_SECRET_TOKEN",),         # merged with built-ins
    cpu_seconds=60.0,
    memory_bytes=512 * 1024 * 1024,   # 512 MiB
    disk_bytes=100 * 1024 * 1024,     # 100 MiB
    pids_limit=64,
    image="python:3.13-slim",
    network_mode="none",              # or "bridge" or "host"
)
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `cwd` | `str` | `Path.cwd()` | Must be absolute. |
| `allowed_paths` | `tuple[str,...]` | `()` | Empty = no filesystem access; non-empty entries may be relative (resolved at construction). |
| `allowed_network_destinations` | `tuple[str,...]` | `()` | Hostname (RFC-1123), IPv4, or IPv4 CIDR. |
| `denied_env_vars` | `tuple[str,...]` | `DEFAULT_DENIED_ENV_VARS` | User entries merged with built-ins. |
| `cpu_seconds` | `float` | `3600.0` (1h) | Wall-clock ceiling. Must be `> 0`. |
| `memory_bytes` | `int` | `512 MiB` | RSS cap; must be `>= 0`. |
| `disk_bytes` | `int` | `100 MiB` | Write-byte cap; must be `>= 0`. |
| `pids_limit` | `int` | `64` | Fork-bomb guard; must be `>= 1`. |
| `image` | `str` | `python:3.13-slim` | Only consumed by `DockerSandbox`. |
| `network_mode` | `Literal["none","bridge","host"]` | `"none"` | Only consumed by `DockerSandbox`. |

`SandboxProfile` is **immutable** (`ConfigDict(frozen=True)`) and rejects
unknown fields (`ConfigDict(extra="forbid")`).

---

## Executors

The sandbox ships three executors, all implementing the
`SandboxExecutor` protocol (`run(command, *, profile, env=None,
stdin=None) -> SandboxResult`):

| Executor | Enforcement model | When to use |
|---|---|---|
| **`NoopSandbox`** | None — runs the command via `subprocess.run(shell=True)` in the host. | Backwards compatibility. Cluster C sets this as the default; pre-cluster-C callers continue to work unchanged. |
| **`SubprocessSandbox`** | `resource.setrlimit` for CPU/FSIZE/NOFILE/PROC, env scrubbing, chroot-equivalent on Linux (requires root). | Local single-tenant runs where Docker is unavailable. |
| **`DockerSandbox`** | `python:3.13-slim` container with `cap_drop=ALL`, `read_only` rootfs, `security_opt=["no-new-privileges:true"]`, profile-driven `mem_limit` / `pids_limit` / `cpu_quota` / `ulimits`. | Production deployments and CI. |

`SubprocessSandbox` and `DockerSandbox` raise
`NotImplementedError` if a method requires OS-level support that the
host lacks (e.g. `chroot` on Windows; bind-mount for paths that are not
in the user's namespace).

### Default behaviour

If you do nothing — if you instantiate `TaskExecutionService` or
`SubprocessRunner` without arguments — both default to `NoopSandbox()`.
This is the explicit promise of Cluster C: **no behaviour change for
existing callers**. To opt into the sandbox, pass
`sandbox=DockerSandbox()` (or `SubprocessSandbox()`) and
`sandbox_profile=SandboxProfile(...)` to the constructor or the
controller factory.

---

## Wire-in points

| Service | Constructor arg | Default |
|---|---|---|
| `TaskExecutionService` (in `seharness.execution.service`) | `sandbox: SandboxExecutor \| None` | `NoopSandbox()` |
| `TaskExecutionService` | `sandbox_profile: SandboxProfile \| None` | `None` (rejected by type-check, falls back to per-call profile) |
| `SubprocessRunner` (in `seharness.validation.runner`) | `sandbox: SandboxExecutor \| None` | `NoopSandbox()` |
| `SubprocessRunner` | `sandbox_profile: SandboxProfile \| None` | `SandboxProfile()` (the fail-closed default) |

Both services have a `__post_init__` (or equivalent) that validates the
sandbox and profile types at construction time — passing the wrong type
raises `TypeError` before any subprocess is launched.

---

## Examples

### Minimal: keep cluster-C defaults

```python
from seharness.execution.service import TaskExecutionService
from seharness.validation.runner import SubprocessRunner

service = TaskExecutionService(repo_root=..., execution_root=...)
runner = SubprocessRunner()
# Both use NoopSandbox behind the scenes; pre-cluster-C behaviour.
```

### Production: Docker isolation, only pypi egress

```python
from seharness.execution.service import TaskExecutionService
from seharness.validation.runner import SubprocessRunner
from seharness.sandbox import DockerSandbox, SandboxProfile

profile = SandboxProfile(
    allowed_network_destinations=("pypi.org",),
    cpu_seconds=120.0,
    pids_limit=128,
)
sandbox = DockerSandbox()

service = TaskExecutionService(
    repo_root=...,
    execution_root=...,
    sandbox=sandbox,
    sandbox_profile=profile,
)
runner = SubprocessRunner(sandbox=sandbox, sandbox_profile=profile)
```

### Test: subprocess sandbox with tight budgets

```python
from seharness.sandbox import SubprocessSandbox, SandboxProfile

profile = SandboxProfile(
    cpu_seconds=2.0,
    pids_limit=32,
    memory_bytes=128 * 1024 * 1024,
)
sandbox = SubprocessSandbox()
result = sandbox.run("pytest -q", profile=profile)
print(result.exit_code, result.duration_s, result.sandbox_violations)
```

### Operator config (`examples/controller.sandbox.yaml`)

See `examples/controller.sandbox.yaml` for a complete controller
configuration that wires `DockerSandbox` into every executor. The
default `examples/controller.yaml` keeps `NoopSandbox` for backwards
compatibility.

---

## Performance overhead

Sandboxed execution adds latency from:

1. **Container launch** (`DockerSandbox`): ~250–500 ms per invocation
   on Linux with image-cached pulls; ~3–10 s for cold pulls. Cluster C
   recommends warming the cache with `docker pull python:3.13-slim`
   during operator onboarding.
2. **Subprocess overhead** (`SubprocessSandbox`): ~5–15 ms per
   invocation from `setrlimit` and `preexec_fn` work. Negligible at
   validator scale.
3. **Profile validation**: <1 ms (Pydantic v2 model instantiation).
4. **Env scrubbing**: 0–2 ms per launch depending on `os.environ` size.

For workloads that launch thousands of short-lived subprocesses per
run, prefer `SubprocessSandbox` over `DockerSandbox`.

---

## Testing notes

`DockerSandbox` requires a reachable Docker daemon; tests that exercise
the live executor skip with `pytest.skip("Docker daemon not
reachable")` when the daemon is absent. Tests that exercise only the
profile and protocol surface do **not** require Docker.

`SubprocessSandbox` uses POSIX `setrlimit` and `preexec_fn` for
isolation; on platforms without these primitives (Windows, certain
container runtimes), the sandbox records the missing capability as a
`sandbox_violations` entry rather than failing the whole run.

**Cluster C Story C5** validates fail-closed semantics at the
**configuration layer** and **API wiring layer** (see
`tests/unit/sandbox/test_redteam.py`). Real red-team payloads (a
fork bomb, a symlink traversal, a DNS exfiltration probe) are NOT
exercised in this suite: those would risk OOM-killing the test host.
The follow-up work (Cluster E "Audit traces", Cluster G "Sandboxed
CI matrix") will exercise real red-team payloads inside disposable
containers where the failure mode is contained.

---

## Known limits

1. **No real-time cancellation** — cancellation propagates between
   phases, but a child that has already been launched waits for
   `cpu_seconds` or its wall-clock to elapse.
2. **No `seccomp` integration in `SubprocessSandbox`** — we rely on
   `RLIMIT_NPROC` and `RLIMIT_CPU` only. A production deployment
   should prefer `DockerSandbox`.
3. **No host network isolation in `SubprocessSandbox`** — DNS
   exfiltration is a residual risk. Use `DockerSandbox` with
   `network_mode="none"` to deny egress entirely.
4. **`NoopSandbox` runs commands via `shell=True`** — this is
   intentional (preserves pre-cluster-C semantics) but means
   **opting into `NoopSandbox` is opting out of the sandbox**. No
   warning is emitted; if you want stricter defaults, set a
   custom factory.

---

## See also

- [Story file](../analysis/2026-07-19-priority-stories.md) — Cluster C
  section.
- [Threat model for the broader harness](../architecture.md) (TBD).
- [Cluster B](configure.md) — controller configuration.
- [Cluster E](extend.md) — audit traces (planned).
