# Operations runbook

This document is the operator-facing guide for the `software-engineering-harness`
repository. It complements the user-facing docs in `docs/user/` and the
release process in `docs/releasing.md` by focusing on **day-to-day operational
signals**: where CI artifacts land, how to read them, and what to check when
something breaks.

> **Status:** v0.2 — operator runbook. Scope is intentionally
> focused: failure recovery, credential rotation, cancellation,
> stuck workers, CI outages, and provider outages, in addition to
> CI artifact triage, dashboard observability, and maintenance
> cadences. Expansion (separate triage / observability docs) is a
> follow-up if the surface area grows.

## Audience

You are reading this because you need to:

- Triage a failed CI run on someone else's PR (or your own).
- Investigate a flaky or broken test in the `flaky-tests.json` report.
- Understand what the dashboard at <https://johrenberger.github.io/software-engineering-harness/>
  is showing you.
- Perform a routine maintenance task (Dependabot merge, base-image refresh,
  secret rotation).

If you are a **user** of the harness trying to run a feature end-to-end,
read `docs/user/run.md` instead.

## Where artifacts land

Every CI run on `main` produces artifacts with a 30-day retention. They are
uploaded by `.github/workflows/ci.yml` and consumed by
`.github/workflows/dashboard.yml`.

| Artifact | Source step | Purpose |
|---|---|---|
| `junit.xml` | pytest | Per-test results (used by dashboard's `totals` + `slowest` blocks). |
| `flaky-tests.json` | `pytest-flake-plugins` (G1c) | Tests that passed after retry vs. tests that failed all retries. |
| `coverage.xml` | pytest `--cov --cov-branch --cov-report=xml` | Branch coverage XML (used by `diff-cover` gate + dashboard). |
| `mutmut-junit.xml` | mutmut | Mutation-test survivors (used by mutation gate + dashboard). |
| `sbom-cyclonedx.json` | `anchore/sbom-action@v0` (G7) | CycloneDX SBOM (only on push to `main`). |
| `scorecard-sarif` | `ossf/scorecard-action` (G5) | OpenSSF Scorecard results. |
| `coverage-badge.svg` | `genbadge` step | Live coverage badge for README. |

Download any artifact via the GH CLI:

```bash
gh run download <run-id> --repo johrenberger/software-engineering-harness --name <artifact-name>
```

Or via the UI: click a workflow run → "Artifacts" section at the bottom of
the summary page.

## Triage: a CI run failed

Follow this decision tree before paging anyone.

### 1. Which workflow failed?

- **`ci`**: 90% of failures are here. Look at the failed leg's logs.
- **`pip-audit`**: a dependency vulnerability was found. Run
  `pip-audit --strict` locally to reproduce, then either bump the dep or
  accept the risk via Dependabot.
- **`codeql`**: static analysis flagged a query. Review the SARIF
  artifact; either fix the code, or `// codeql[...]: ignore this query`
  with a justification comment.
- **`openssf-scorecard`**: weekly cron — usually non-actionable unless
  the score dropped >0.2 since the last run.
- **`dashboard`**: usually a render script crash; check the step that
  reads artifacts first.

### 2. Is the failure flaky?

Check `flaky-tests.json`. The job summary at the bottom of the run page
also shows it inline. Two cases:

- **Listed under "flaky tests (passed after reruns)"**: not a hard
  failure — the test passed within the retry budget. Consider opening
  an issue to address the flakiness root cause (timing, shared state,
  network) but the PR is not blocked.
- **Listed under "broken tests (failed after exhausted retries)"**:
  this is a hard failure. The PR is blocked.

### 3. Is the failure a coverage drop?

Look at the `coverage-diff-check` step. If it failed with "diff coverage
below 80%", the PR added lines that are not exercised by tests. Either
add tests for the new lines, or refactor to not need them.

If it failed with "combined coverage below 89%", that's a regression in
overall coverage from a refactor. The floor is set in `pyproject.toml`
under `[tool.coverage.report]` → `fail_under = 89`. Bumping the floor
requires a separate lift PR (see the G1 evidence file for context).

### 4. Is the failure a mutation-test regression?

The `mutation-gate` step fails if more than `MUTATION_FAIL_UNDER` percent
of mutants survive (default 20%). Check `mutmut-junit.xml` for the
specific surviving mutants. Adding a test that *kills* the mutant is the
correct fix; weakening the assertion to pass is **not** — the gate
exists to enforce RED-before-GREEN on real behavior.

## Triage: dashboard looks wrong

The dashboard reads from the **last successful CI run on `main`**. If it
looks stale or broken:

1. **Stale data (old run shown)**: check `.github/workflows/dashboard.yml`
   is being triggered on push. The trigger is `on: push: branches: [main]`.
   If the latest push skipped dashboard re-publish, that's a workflow
   bug; check the dashboard run history.

2. **Empty blocks (e.g. `totals: 0`)**: usually means an artifact
   failed to parse. Check the `render-dashboard` step log in
   `.github/workflows/ci.yml`. The renderer is `scripts/render_dashboard.py`;
   re-rendering locally is:

   ```bash
   python scripts/render_dashboard.py --offline  # reads from .openclaw-runs/
   ```

3. **Live build-history block missing**: requires network access from the
   dashboard runtime. The renderer fetches `actions/runs` via the
   GitHub API; a 401/403 means the workflow token lost scope. This is
   non-critical — the rest of the dashboard still renders.

## Triage: a `.openclaw-runs/` directory is huge

See the **Runbook: a `.openclaw-runs/` directory is huge** section
below for the recovery procedure.

## Maintenance cadences

| Cadence | Task | Where |
|---|---|---|
| **Weekly (Mondays 06:00 UTC)** | `pip-audit` cron surfaces new dep vulns. | `.github/workflows/pip-audit.yml` |
| **Weekly (Wednesdays 03:00 UTC)** | CodeQL re-scan. | `.github/workflows/codeql.yml` |
| **Weekly (Fridays 06:00 UTC)** | OpenSSF Scorecard cron. | `.github/workflows/openssf-scorecard.yml` |
| **Weekly (Dependabot)** | Grouped PRs for pip + GitHub Actions updates. | `.github/dependabot.yml` |
| **On every push to `main`** | Dashboard re-publish + SBOM + attestation. | `.github/workflows/dashboard.yml`, G7 steps in `ci.yml` |
| **Quarterly** | Refresh Python base-image digest in `docker/Dockerfile`. | `docker/Dockerfile` line referencing `python:3.13-slim@sha256:...` |
| **As needed** | Rotate `TELEGRAM_BOT_TOKEN`, `CODECOV_TOKEN`, etc. | `.env.example` documents the variables |

### Refreshing the base-image digest

The Dockerfile pins `python:3.13-slim` to a manifest-list SHA256. When
upstream re-tags the image (e.g. security patch), the digest changes.
Procedure:

```bash
# 1. Find the current digest from Docker Hub
docker buildx imagetools inspect python:3.13-slim --format '{{json .Manifest}}'
# 2. Copy the digest into docker/Dockerfile FROM line
# 3. Run the G10 contract tests (test_python_base_image_uses_known_digest
#    will need an update to the new digest in its allowlist).
# 4. Commit + PR. CI will verify the rest.
```

The test allowlist is in `tests/unit/ci/test_g10_pinned_dependencies.py`;
update the `KNOWN_GOOD_DIGESTS` set to include the new digest before
the PR is mergeable.

## Escalation

When the runbook above does not resolve the issue within ~30 minutes:

1. Check the project board / open issues for known incidents.
2. Review the most recent merged PR for `feat:` / `fix:` commits touching
   the affected subsystem — context for *what changed* is usually the
   fastest signal.
3. If a security issue: follow `SECURITY.md` — do not file a public
   issue.

## Operator runbooks

This section expands the runbook for the failure modes that
operators see in production but not in unit tests. Each subsection
names the **symptom**, the **likely cause**, and the **recovery
steps**.

### Runbook: a phase failed and the run is paused

**Symptom:** `/status` or the dashboard shows a run in
`BLOCKED` outcome for one phase; the orchestrator logs say
`PhaseOutcome.BLOCKED` with a `reason`.

**Likely cause:** Either (a) `BudgetExhausted` was raised by
`BudgetTracker.enforce()` at the phase boundary, or (b) the phase
handler returned `BLOCKED` because of a deterministic check failure.

**Recovery:**

1. Read `run_dir/trace.jsonl` and look for the last `phase.<name>.end`
   event. The `attributes.reason` field carries the human-readable
   reason.
2. If the cause is a budget, decide:
   - **Extend the budget**: bump the relevant axis on the next
     `OrchestratorConfig` and `/resume <run_id>`.
   - **Cancel**: `/cancel <run_id>` and start a new run with a
     tighter scope.
3. If the cause is a deterministic check failure (e.g. validation
   RED), inspect `run_dir/execution/<task_id>/red/` and fix the
   underlying test.
4. `/resume <run_id>` always picks up from the last completed phase.
   Spec-drift between the original `feature_description` and the
   resume's `feature_description` is detected and rejected.

### Runbook: a worker is stuck (lease not renewed)

**Symptom:** `LeaseStore` reports `LeaseConflict` on `acquire()`
for a `run_id` that was running 5 minutes ago; `list_leases()`
shows the lease is held by a worker that is no longer emitting
heartbeats.

**Likely cause:** The orchestrator process crashed (OOM, kernel
panic, container restart) without calling `release()`. The lease
is still held because `release()` did not run.

**Recovery:**

1. Confirm the worker is dead: `ps -ef | grep <worker_pid>`.
2. Wait for the lease to expire. `default_lease_ttl_seconds()`
   defaults to `ORCHESTRATOR_LEASE_TTL_SECONDS` (env override;
   default 600 s).
3. Or, force-expire the lease by deleting the lease store file:
   `<exec_root.parent>/..<exec_root.name>_leases/<run_id>.json`.
   The next `start_run` call will succeed.
4. If the run was a long-running model inference, expect
   token/cost consumption to be wasted; budget enforcement will
   catch the next phase.

### Runbook: credential rotation

**Symptom:** A secret in `.env` or GitHub Secrets has been
exposed (or is about to expire) and must be rotated.

**Likely cause:** Scheduled rotation, incident response, or a
Dependabot PR that bumped a vulnerable library.

**Recovery:**

1. Generate the new credential in the upstream system
   (GitHub PAT page, OpenAI dashboard, Anthropic console).
2. Update the secret in GitHub Secrets (`Settings → Secrets and
   variables → Actions → New repository secret`).
3. For the local `.env`, update the entry and restart any
   running orchestrator.
4. **Audit traces for the old credential**: search for the old
   token in `.openclaw-runs/orchestrator/*/trace.jsonl`. If it
   appears, the secret was leaked; treat the trace file as
   compromised and rotate immediately.
5. `SecretRedactor` scrubs known patterns at write-time, so a
   literal token value WILL be replaced with `***REDACTED***`
   before it hits disk. But if the token format is new and not
   in the pattern list, add it to
   `src/seharness/observability/redactor.py:_PATTERNS` and rerun
   the unit tests.

### Runbook: cancellation did not stop the run

**Symptom:** `/cancel <run_id>` returned success but the run is
still in `RUNNING` state 5 minutes later.

**Likely cause:** The cancellation token was flipped, but the
phase handler blocked on a long-running subprocess that did not
honor the cancellation signal (e.g. a model inference that holds
the event loop).

**Recovery:**

1. `ps -ef | grep <worker_pid>` and `kill -9 <worker_pid>`. The
   subprocess process group is killed because the orchestrator
   uses `os.setsid` and `process_group=True`.
2. `/status <run_id>` should now show `CANCELLED`.
3. If the run is stuck in `CANCELLED` (terminal), `/resume
   <run_id>` will start a fresh lease and pick up from the last
   completed phase.
4. If the same hang happens on every run, the offending
   adapter is blocking the event loop; file an issue with the
   trace excerpt.

### Runbook: CI outage (GitHub Actions down)

**Symptom:** PRs are not getting CI feedback; `ci.yml` runs are
not appearing on the PR.

**Likely cause:** GitHub Actions incident; check
<https://www.githubstatus.com/>.

**Recovery:**

1. If the outage is brief (< 30 min): wait. The orchestrator
   does not depend on CI; only the **delivery** phase does.
2. If the outage is longer: switch to local delivery by setting
   `DELIVERY_BACKEND=local` in `.env`. The orchestrator will
   create the branch + commit locally instead of pushing a PR.
   The PR is created manually after GitHub is back.
3. After GitHub recovers: re-trigger `ci.yml` via
   `gh workflow run ci.yml --ref <branch>`. The readiness gate
   will resume checking the PR's check status.

### Runbook: provider outage (OpenAI / Anthropic / Codex down)

**Symptom:** Every run fails in the same phase with `ModelError`
or `Timeout`; the error message names the provider.

**Likely cause:** Provider-side incident. Check the provider's
status page.

**Recovery:**

1. If the outage is brief (< 5 min): the rate-limit retry-with-
   backoff in `ModelRouter` will handle it. Do nothing.
2. If the outage is longer: switch providers by setting
   `MODEL_BACKUP=anthropic` (or vice-versa) in `.env`. The
   `ModelRouter` will fall back to the secondary provider on
   hard failures.
3. If both providers are down: cancel in-flight runs
   (`/cancel <run_id>`) and pause the intake bot by setting
   `TELEGRAM_INTAKE_ENABLED=false`. Existing runs are safe to
   resume later — the orchestrator checkpoints `phase` + `ctx`
   on every phase boundary.
4. After the outage: re-enable intake and `/resume` the paused
   runs.

### Runbook: PyPI Trusted Publisher misconfigured

**Symptom:** A `v*` tag was pushed; the release exists on
GitHub Releases; PyPI shows nothing.

**Likely cause:** The PyPI Trusted Publisher for this project
is not configured (or was rotated). See `docs/releasing.md`.

**Recovery:**

1. Check the workflow log for `publish-to-pypi` step; it logs
   `PyPI publish skipped: Trusted Publisher not configured` if
   this is the cause.
2. Set up the Trusted Publisher at
   <https://pypi.org/manage/account/publishing/>. The workflow
   name is `release.yml`; the environment is `pypi`.
3. Re-run the workflow on the same tag via `gh workflow run
   release.yml --ref vX.Y.Z`. The publish step will now succeed.

### Runbook: a `.openclaw-runs/` directory is huge

The orchestrator writes per-run evidence to `.openclaw-runs/orchestrator/<run_id>/`.
Each run is bounded (a few MB) but accumulated across many runs they can
fill the disk.

There is no automatic cleanup yet (see the honesty matrix of
`docs/architecture-overview.md`). To reclaim space manually:

```bash
# Delete runs older than 30 days
find .openclaw-runs/ -maxdepth 2 -type d -mtime +30 -exec rm -rf {} +
```

Each run is independently self-contained (all artifacts + trace in one
directory), so deleting old runs is safe.

## See also

- `docs/user/run.md` — running the harness end-to-end.
- `docs/user/traces.md` — orchestrator trace record schema.
- `docs/user/sandbox.md` — sandbox profiles and their trade-offs.
- `docs/releasing.md` — release-time runbook (different cadence).
- `docs/architecture-overview.md` — service graph + honesty matrix.
- `docs/engineering-dashboard.md` — what the dashboard shows.
- `docs/evidence/` — per-PR evidence files for audit trail.
- `SECURITY.md` — vulnerability reporting.
