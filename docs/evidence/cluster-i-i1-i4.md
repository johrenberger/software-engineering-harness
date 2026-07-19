# I1 + I4 — README honesty rewrite

**Status:** ✅ MERGED
**Branch:** `agent/i1-i4-readme-honesty` → `main`
**PR:** [#35](https://github.com/johrenberger/software-engineering-harness/pull/35) — merged at `ba57fb0`
**Commit:** `8367578` — `docs(readme): I1+I4 honesty rewrite with status section + contract tests`

## What landed

The README was rewritten from **68 lines to ~210 lines** with three
explicit, contract-pinned honesty commitments:

1. **Status section (I1)**: a single source of truth for what the
   harness does *today*, distinguishing:
   - ✅ What works end-to-end (13 rows, each pinned to a test or workflow)
   - ⚠️ What's partial or planned (5 rows, with cluster + story IDs)
   - ❌ What we explicitly are NOT doing (3 bullets)

2. **Alpha framing (I1)**: explicit `v0.1.0 / Development Status :: 3 - Alpha`
   in the lead paragraph so users calibrate expectations.

3. **PyPI gap made visible (I1)**: `pip install seharness` is shown as
   the aspirational command but flagged "Not yet published" with a link
   to the G18 follow-up story.

4. **Honesty contracts (I4)**: 15 contract tests in
   `tests/unit/docs/test_readme_honesty.py` pin the structural
   commitments and forbid claims contradicted by the code:
   - Status section must exist + distinguish works/partial/not-doing
   - PyPI gap must be explicit
   - v0.1.0 / Alpha framing must be present
   - Cannot claim `pip install seharness` works without a caveat
   - Cannot claim public dashboard bind works
   - Mentioning traces/sandbox must link to `docs/user/{traces,sandbox}.md`
   - SECURITY.md must be linked
   - Live dashboard URL (when referenced) must respond 200 (network-marked skip)

## Why this story matters

Most "is this ready?" questions about a project get answered by reading
the README. A README that promises more than the code delivers sets
users up for wasted time and erodes trust. I1 + I4 flip that: the
README is now the **most accurate single document** in the repo, with
machine-checked enforcement of its honesty.

## Files touched

- `README.md` — 68 → 210 lines, restructured.
- `pyproject.toml` — added `network` marker for skip-if-offline tests.
- `tests/unit/docs/test_readme_honesty.py` — NEW (15 contract tests).

## Test count growth

| Stage | Tests |
|---|---|
| Pre-I1+I4 | 1552 |
| After I1+I4 | **1589** (+37 I1+I4 tests across both files; the net delta is |
| | +37: 15 honesty contracts + the dispatcher error-path tests that |
| | were already added in PR #34 are reflected in the cumulative). |

## CI run

- PR #35 CI: 29705748202 ✅ pass (both legs — first PR to exercise
  the G3 matrix from PR #32).
- Post-merge CI on main: 29705814369 ✅ both legs (3.12 + 3.13).
  Coverage 89.17% (gate 89% reached).
- Post-merge dashboard re-publish: 29705814376 ✅
  dashboard redeployed to <https://johrenberger.github.io/software-engineering-harness/>.

## Gotchas captured

- `urllib.request.urlopen` from this sandbox cannot reach
  `sigstore.dev` or `token.actions.githubusercontent.com`, but it CAN
  reach `pypi.org` (HTTP 200) and the GitHub Pages site (200 from
  outside this sandbox; 404 from this sandbox because the sandbox's
  egress proxy redirects). The `@pytest.mark.network` marker on the
  live-URL test allows CI runs in restricted environments to skip
  cleanly.
- `README` must remain >2 KB; a regression that drops it below that
  threshold (or deletes the Status section) will fail CI.
- The "first paragraph" check requires the **second** paragraph (the
  description) to mention "Python" or "framework" — the title alone
  doesn't count.
