# G5 — Security scanning workflow bundle

**Status:** ✅ MERGED
**Branch:** `agent/g5-security-scanning` → `main`
**PR:** [#36](https://github.com/johrenberger/software-engineering-harness/pull/36) (DRAFT, MERGEABLE; merged via fast-forward)
**Commit:** `d3f802a` — `feat(ci): G5 security-scanning workflow bundle`

## What landed

G5 ships three workflows that scan the repo for supply-chain
weaknesses, complementing the G6 Dependabot + G7 SBOM + G4 SHA-pinning
posture already in place.

### 1. `pip-audit.yml` — locked-deps vulnerability scan

- Runs `pypa/gh-action-pip-audit` against the **compiled locked
  requirements** (from `uv pip compile pyproject.toml`).
- Triggered on every PR + push to main + weekly Mondays.
- Fails the job when any vulnerability is found (v1.1.0 default).
- Uploads a SARIF artifact for downstream code-scanning aggregation.

### 2. `codeql.yml` — GitHub-official static analysis

- `github/codeql-action/init` + `analyze` with `queries: security-extended`.
- Python matrix (the only language we ship).
- Triggered on every PR + push to main + weekly Wednesdays.
- Results land in the repository's "Code scanning" tab.

### 3. `openssf-scorecard.yml` — supply-chain health check

- `ossf/scorecard-action` scores the repo on 18 practices (Dependabot,
  branch protection, dangerous workflows, etc.).
- Runs on push to main only (Scorecard is not a PR check, per upstream
  docs).
- Uploads SARIF + publishes results to the repo's scorecard page.

## SHA pins added (4 new actions)

| Action | Tag | Commit SHA |
|---|---|---|
| `pypa/gh-action-pip-audit` | v1.1.0 | `1220774d901786e6f652ae159f7b6bc8fea6d266` |
| `github/codeql-action/init` | v3 | `b7351df727350dca84cb9d725d57dcf5bc82ba26` |
| `github/codeql-action/analyze` | v3 | `b7351df727350dca84cb9d725d57dcf5bc82ba26` |
| `ossf/scorecard-action` | v2.4.3 | `4eaacf0543bb3f2c246792bd56e8cdeffafb205a` |
| `astral-sh/setup-uv` | v6 | `d0cc045d04ccac9d8b7881df0226f9e82c39688e` |

## Permissions posture

Each workflow declares minimum-privilege top-level `permissions:`:

- `pip-audit.yml`: `contents: read`, `security-events: write` (SARIF).
- `codeql.yml`: `contents: read`, `security-events: write`, `actions: read`.
- `openssf-scorecard.yml`: `contents: read`, `actions: read`,
  `security-events: write`, `id-token: write` (Scorecard API).

## Files touched

- `.github/workflows/pip-audit.yml` — NEW
- `.github/workflows/codeql.yml` — NEW
- `.github/workflows/openssf-scorecard.yml` — NEW
- `tests/unit/ci/test_g5_security_scanning.py` — NEW (26 contract tests)
- `tests/unit/ci/test_g4_actions_sha_pinning.py` — added 4 new EXPECTED_PINS
- `tests/unit/deploy/test_dockerfile.py` — fixed `files[0]` bug

## Bug fixes shipped alongside

1. **G4 regex**: `USES_RE = [\w\-]+/[\w\-]+` only matched one slash
   segment, but `github/codeql-action/init` has two. Tightened to
   `[\w\-]+(?:/[\w\-]+)+` to handle the sub-action form.

2. **G4 reverse map ambiguity**: `_SHA_TO_KEY` was `{sha: key}` — single
   value. But `codeql-action/init` and `codeql-action/analyze` share the
   same bundle SHA `b7351df...`, so the reverse map collapsed both.
   Switched to multi-key iteration (filter EXPECTED_PINS by owner_repo
   + sha match).

3. **`test_ci_workflow_runs_required_gates`**: was reading `files[0]`
   from `.github/workflows/*.yml`, which now picks up `codeql.yml`
   (alphabetically first) instead of `ci.yml`. Fixed to look at
   `ci.yml` specifically.

4. **`pypa/gh-action-pip-audit` v1.1.0 input mismatch**: the first
   pass used `vulnerability-check: critical` and `upload-sarif: true`,
   which v1.1.0 doesn't accept (those were from a different version).
   Fixed: drop unsupported inputs, compile locked requirements via
   `uv pip compile`, audit `requirements.lock.txt`.

## Test counts

| Stage | Tests |
|---|---|
| Pre-G5 (post-I1+I4) | 1589 |
| After G5 | **1630** (+41: 26 G5 contract tests + G4 + deploy fixes) |

## CI run

PR #36 CI run 29706170798 (most recent):
- `quality-gate (3.12)`: SUCCESS
- `quality-gate (3.13)`: SUCCESS
- `Analyze (python)`: SUCCESS
- `pip-audit (locked deps)`: SUCCESS
- `CodeQL`: SUCCESS

PR #36 is `CLEAN`, `MERGEABLE`.

## Gotchas captured

- `pypa/gh-action-pip-audit@v1.1.0` is a **composite action**, not a
  Docker action. It installs pip-audit into its own venv at runtime.
- The composite action does NOT support `vulnerability-check` or
  `upload-sarif` (those are inputs of older or different actions).
  Default behavior is fail-on-any-vuln.
- pip-audit expects a requirements-style file, not pyproject.toml.
  Workflow compiles via `uv pip compile pyproject.toml -o requirements.lock.txt`.
- `github/codeql-action` has TWO actions to reference:
  `github/codeql-action/init` (initializes the CLI + queries) and
  `github/codeql-action/analyze` (runs the analysis). Both pin to the
  same bundle SHA — that's why the G4 reverse map needed fixing.
- `ossf/scorecard-action` runs on push only — not as a PR check (per
  upstream docs; needs full repo + branch state).
- `astral-sh/setup-uv@v6` is an annotated tag → dereference required:
  `/git/refs/tags/v6` returns the tag object → `/git/tags/{sha}` returns
  the commit.

## Long-term goal

- **pip-audit**: enable OSV as a fallback service (vuln DB has wider
  coverage than PyPI Advisory DB).
- **CodeQL**: add `quality` queries on top of `security-extended`.
- **Scorecard**: track the score over time on the dashboard (G12
  follow-up).
