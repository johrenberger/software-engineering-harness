# Software Engineering Harness

Framework-neutral Python harness that standardizes feature delivery
across OpenClaw agents (Minimax and Codex) using deterministic workflow
state, validated artifacts, automated testing/remediation, Telegram
intake, and GitHub pull-request delivery.

The harness is **built** but **not yet ready for external use**: it is at
version 0.2.0 (`Development Status :: 3 - Alpha`). The v0.2.0 source
tarball + wheel are published as a GitHub Release (install via the
release asset URL — see [Install](#install)); PyPI publish is best-effort
and requires a one-time Trusted Publisher setup (see
[Status](#status)). Several capabilities are still in the planning or
construction stage. This README is the canonical entry point and is
updated alongside the code — **if a claim here is not backed by a test
or a runnable example, it is flagged in [Status](#status)**.

## Status

This section is the source of truth for what the harness actually does
*today*. If you are evaluating the project, read this section first.

### What works end-to-end ✅

| Capability | Verified by |
|---|---|
| Deterministic 12-phase pipeline (`feature_request` → `repository_discovery` → `specification` → `planning` → `implementation` → `validation` → `remediation` → `review` → `draft_pr` → `ci` → `ready` → `completed`) | 1874 unit + integration tests pass in <30 s; mutation-killer tests cover phase semantics. |
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
| **PyPI release** (`pip install seharness`) | **DONE (best-effort).** Soft-publish automation landed in v0.2.0 (PR #55): pushing a `v*` tag always produces a public GitHub Release with wheel + sdist + SBOM + Sigstore-provenance bundle attached. PyPI publish itself logs and skips unless the Trusted Publisher is configured. Install today: `pip install https://github.com/johrenberger/software-engineering-harness/releases/download/v0.2.0/seharness-0.2.0-py3-none-any.whl`. To enable `pip install seharness` on PyPI: one-time Trusted Publisher setup at <https://pypi.org/manage/account/publishing/>. | Cluster G9 (DONE soft-publish) + G18 (P2 if you want `pip install seharness`). |
| **Cancelled-run resumability** (`/resume <run_id>`, `/cancel <run_id>`) | **DONE in-process + cross-process.** `/cancel <run_id>` flips the per-run `CancellationToken` and kills the subprocess process group (E4a + E4b, shipped in v0.2.0). `/resume <run_id>` picks up from the last completed phase using the persisted `phase` + `ctx` + `feature_description` on `RunRecord` (E3 state model — `Orchestrator.start_run(resume_from_run_id=...)`). The `FileRunLedger` carries the cursor across process restarts; spec-drift between the original and the resume is detected and rejected. | Cluster E stories E4a + E4b (DONE in v0.2.0); E3 state model shipped. |
| **Human approval gates** between phases | The handler surface exists but the policy layer (which phases require approval, who can approve, what triggers a re-prompt) is design-stage. | Cluster E story E7 (P1). |
| **Cluster F: provider/credential config** | F1 (provider docs + env-var credential contract) shipped in v0.2.0 (`docs/providers.md` + 13 contract tests). The multi-provider config file format (`config/providers.toml`) and the multi-credential loading flow (file → env → secrets manager) are not finalized. | Cluster F1 (DONE) + F2–F8 (P1). |
| **Cluster H: hardening** | **DONE.** H1 (rate-limit retry-with-backoff) + H2 (`SuspiciousPayloadFilter` rejecting binary / encoded payloads) shipped before v0.2.0; defended by unit tests in `tests/unit/security/test_payload_filter.py` + `tests/unit/models/test_rate_limit_retry.py`. | Cluster H1 + H2 (DONE in v0.2.0). |
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

### From a release asset (current recommended path)

```bash
# v0.2.0 wheel (signed with Sigstore; SBOM + provenance in the release):
python -m pip install \
  https://github.com/johrenberger/software-engineering-harness/releases/download/v0.2.0/seharness-0.2.0-py3-none-any.whl
```

Each release page lists every artifact: `seharness-X.Y.Z-py3-none-any.whl`,
`seharness-X.Y.Z.tar.gz`, the Sigstore signatures, and the CycloneDX SBOM.

### From PyPI (requires one-time setup)

```bash
pip install seharness
```

**Status:** *Soft-publish is enabled.* The release workflow
attempts to publish to PyPI on every `v*` tag push; if a Trusted
Publisher isn't configured for this project, the publish step logs
"NOT PUBLISHED" and the GitHub Release still ships with all
artifacts. To enable `pip install seharness`:

1. Add a Trusted Publisher entry at
   <https://pypi.org/manage/account/publishing/> for project
   `seharness` pointing at `.github/workflows/release.yml`.
2. (Optional) Create a GitHub `pypi` environment with required
   reviewers for a manual approval gate.

See [docs/releasing.md](docs/releasing.md) for the full workflow.

### Docker

```bash
docker build -t seharness:0.2.0 -f docker/Dockerfile .
docker compose -f docker/docker-compose.yml up
```

The image is based on `python:3.13-slim`. Image size is not currently
reported in CI; check `docker images seharness:0.2.0` after building.

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
| `/resume <run_id>` | Resume a paused run. *(Works across process restarts since E3.)* |
| `/cancel <run_id>` | Cancel a running run. *(In-process since v0.2.0; cross-process cancel-resume supported since E3.)* |
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
- [Operations runbook](docs/operations.md) *(Cluster I, I3)*
- [Providers & credentials](docs/providers.md) *(Cluster F, F1)*
- [Priority tracker](docs/analysis/2026-07-19-priority-stories.md)

## Project layout

```
src/seharness/
├── cli.py                 # `seharness` entry point
├── orchestrator/          # 12-phase pipeline + runners
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
