# Software Engineering Harness

Framework-neutral Python harness that standardizes feature delivery
across OpenClaw agents (Minimax and Codex) using deterministic workflow
state, validated artifacts, automated testing/remediation, Telegram
intake, and GitHub pull-request delivery.

The harness is **built** but **not yet ready for external use**: it is at
version 0.1.0 (`Development Status :: 3 - Alpha`), has no PyPI release,
and several documented capabilities are still in the planning or
construction stage. This README is the canonical entry point and is
updated alongside the code — **if a claim here is not backed by a test
or a runnable example, it is flagged in [Status](#status)**.

## Status

This section is the source of truth for what the harness actually does
*today*. If you are evaluating the project, read this section first.

### What works end-to-end ✅

| Capability | Verified by |
|---|---|
| Deterministic 6-phase pipeline (spec → implement → test → review → document → release) | 1552 unit + integration tests pass in <30 s; mutation-killer tests cover phase semantics. |
| Telegram intake bot with 9 commands (`/start /help /status /runs /feature /pr /resume /cancel /dashboard`) | `tests/unit/telegram/` covers auth + dispatch + stub transport + mutation-killers. |
| Two-runner model: `StubRunner` (in-memory) and `LocalCommandRunner` (subprocess, with timeout) | `tests/unit/orchestrator/test_runner_coverage.py` (100% coverage). |
| Sandbox layer (Docker, subprocess, Noop) with threat-modeled isolation | Cluster C slices C1–C5; `examples/controller.sandbox.yaml` shows operator config. |
| Run traces with secret redaction (`tests/unit/observability/test_redactor.py`) | Cluster E stories E5+E6; `docs/user/traces.md` documents the JSONL format. |
| Engineering dashboard published via GitHub Pages | `dashboard.yml` workflow runs on every push to `main`; live at <https://johrenberger.github.io/software-engineering-harness/>. |
| SBOM (CycloneDX 1.6) + Sigstore-signed SLSA L1 provenance on every CI run | G7 cluster, `.github/workflows/ci.yml` produces both artifacts; `docs/releasing.md` documents verification. |
| Dependabot for pip + GitHub Actions, weekly | `.github/dependabot.yml` + 16 contract tests. |
| Coverage gate (`fail_under = 89`) + per-PR diff-cover | `tests/unit/ci/test_g1_lift_coverage_workflow.py` + `tests/unit/ci/test_g1b_diff_cover_workflow.py`. |
| Mutation gate (`mutmut`) on changed files | `tests/unit/ci/test_g2s2_mutation_gate_workflow.py`; surfaces a per-PR percentage on the PR. |
| Action SHA pinning across all CI workflows | G4 cluster; `.github/workflows/ci.yml` and `dashboard.yml` use 9 SHA-pinned `uses:` refs. |
| Python 3.12 + 3.13 matrix CI (G3) | `.github/workflows/ci.yml`; `fail-fast: false`. |
| Vulnerability reporting policy | `SECURITY.md`; private reporting via GitHub Security Advisories. |

### What's partial or planned ⚠️

| Capability | Status | Tracking |
|---|---|---|
| **PyPI release** (`pip install seharness`) | Not yet published. Package is at v0.1.0 / Alpha; PyPI publish is the G18 follow-up. Today: `pip install -e ".[dev]"` from a clone. | Cluster G story G18 (P2). |
| **Cancelled-run resumability** (`/resume <run_id>`) | The handler is wired and dispatch-tested, but the underlying orchestrator state machine does not yet persist enough to resume across a process restart. `/cancel` (E4) is also a follow-up. | Cluster E stories E3 (state model) + E4 (cancellation). |
| **Human approval gates** between phases | The handler surface exists but the policy layer (which phases require approval, who can approve, what triggers a re-prompt) is design-stage. | Cluster E story E7 (P1). |
| **Cluster F: provider/credential config** | The CLI accepts env vars but the multi-provider config file format (`config/providers.toml`) and credential-loading flow are not finalized. | Cluster F stories F1–F8 (P1). |
| **Cluster H: hardening** | No work has started on the H-cluster hardening stories (rate-limit fallbacks, suspicious-payload filtering). | Cluster H stories H1–H2 (P1). |
| **Telegram live poll test** | Stub-transport tests cover the contract, but live `python-telegram-bot` polling against a real chat is integration-only and requires a bot token + chat id the CI environment does not have. | `tests/integration/telegram/test_live_poll.py` (P2 follow-up). |

### What we explicitly are NOT doing ❌

- **Multi-LLM routing at runtime**: the harness is framework-neutral in design but currently only runs against a single configured agent at a time. Provider switching requires restart.
- **Self-hosted server mode**: the dashboard binds to `127.0.0.1` only and is intended for local development + GitHub Pages publication. There is no auth layer and no public-bind path.
- **API stability guarantees** before v1.0.0: pre-1.0 we reserve the right to break any public-facing CLI or Python API between minor versions.

## Install

### From a clone (current recommended path)

```bash
git clone https://github.com/johrenberger/software-engineering-harness.git
cd software-engineering-harness
pip install -e ".[dev]"
```

The `[dev]` extra installs pytest, ruff, mypy, bandit, pip-audit, and
mutmut (all of which the project's own CI runs).

### From PyPI

```bash
pip install seharness
```

**Status:** *Not yet published.* Track G18 in
[docs/analysis/2026-07-19-priority-stories.md](docs/analysis/2026-07-19-priority-stories.md)
for the release automation story.

### Docker

```bash
docker build -t seharness:0.1.0 -f docker/Dockerfile .
docker compose -f docker/docker-compose.yml up
```

The image is based on `python:3.13-slim`. Image size is not currently
reported in CI; check `docker images seharness:0.1.0` after building.

## Usage

### Run the Telegram bot

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_ALLOWED_CHAT_IDS=111,222
harness-telegram-bot
```

The bot supports these commands (see `docs/user/run.md` for the full
contract and `tests/unit/telegram/` for dispatch tests):

| Command | Description |
|---------|-------------|
| `/start` | Show help. |
| `/help` | Show help. |
| `/status` | Current slice + last green commit. |
| `/runs` | Recent run ids. |
| `/feature <repo> <req>` | Start a feature run. |
| `/pr <branch>` | Check PR readiness. |
| `/resume <run_id>` | Resume a paused run. *(See Status ⚠️.)* |
| `/cancel <run_id>` | Cancel a running run. *(See Status ⚠️.)* |
| `/dashboard` | Text dashboard summary. |

### Run the dashboard server

```bash
harness-dashboard
# Open http://127.0.0.1:8765
```

The dashboard exposes:

- `GET /` — HTML snapshot.
- `GET /api/state` — JSON snapshot.
- `GET /healthz` — `200 ok`.

The dashboard binds to `127.0.0.1` only. Public bind is rejected by the
server's host-allowlist (`ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})`).

### Run the CLI

```bash
seharness --help
```

## Documentation

- [Install](docs/user/install.md)
- [Configure](docs/user/configure.md)
- [Run](docs/user/run.md)
- [Extend](docs/user/extend.md)
- [Sandbox threat model](docs/user/sandbox.md)
- [Run traces](docs/user/traces.md) *(Cluster E)*
- [Releasing the harness](docs/releasing.md)
- [Engineering dashboard](docs/engineering-dashboard.md)
- [Priority tracker](docs/analysis/2026-07-19-priority-stories.md)

## Project layout

```
src/seharness/
├── cli.py                 # `seharness` entry point
├── orchestrator/          # 6-phase pipeline + runners
├── telegram/              # Telegram service + transport (protocol + stub)
├── telegram_runtime/      # python-telegram-bot wiring + dispatch
├── controller/            # Application service + run ledger
├── sandbox/               # Docker, subprocess, Noop sandboxes
├── validation/            # Classifiers + retry + remediation
├── observability/         # Trace writer + secret redactor
├── dashboard/             # aiohttp server + renderer
└── ci/                    # Stub clients used by the dashboard's contract tests

docs/user/                 # Operator-facing docs (install/configure/run/...)
docs/analysis/             # Internal review documents
docs/evidence/             # Per-cluster evidence files (merged-cluster status)
examples/                  # Operator config (e.g. controller.sandbox.yaml)

.github/workflows/
├── ci.yml                 # test + coverage + mutation + SBOM + provenance
└── dashboard.yml          # publish dashboard to GitHub Pages
```

## Security

To report a vulnerability, **do not file a public issue** — use
[GitHub Security Advisories](https://github.com/johrenberger/software-engineering-harness/security/advisories/new)
or email `security@openclaw.eu`. See [SECURITY.md](SECURITY.md) for
the response timeline and supported versions.

## License

MIT.

## Contributing

Issues and PRs welcome. Before opening a PR:

1. Run `uv run pytest` locally — all tests must pass.
2. Run `uv run ruff format` + `uv run ruff check` — both must be clean.
3. Run `uv run mypy --strict src/seharness` — must report no issues.
4. Check that `dashboard/assets/data.js` reflects any dashboard-data changes
   (the contract tests in `tests/unit/ci/test_g12*_workflow.py` pin this).
5. If your PR adds new external integrations, also update
   `.github/dependabot.yml` and the SHA-pinned action refs in
   `.github/workflows/`.

For larger changes, open an issue first to discuss scope — the
[Priority tracker](docs/analysis/2026-07-19-priority-stories.md) lists
the P1/P2 stories already in flight.
