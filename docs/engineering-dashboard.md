# Engineering dashboard

A single-page engineering dashboard for the SEHarness project, served
via GitHub Pages and updated on every push to `main`. No per-run pages.

## Why a single page

The user originally requested "single page updated each run (not per-run
pages)". A single index.html with a data sidecar is the simplest model
that:

- renders the **latest** snapshot on every visit (no stale links)
- uses git diffs to review dashboard changes (one file per push)
- avoids a Jekyll/Ruby toolchain dependency
- loads Chart.js locally (no CDN → no third-party tracking, no rate limits)

## Artifacts consumed

The dashboard reads four CI artifacts already produced by our pipeline,
plus a live fetch from the GitHub Actions REST API:

| Artifact | Source step | Sections it feeds |
|---|---|---|
| `junit.xml` | pytest | `totals`, `slowest` |
| `flaky-tests.json` | `pytest-flake-plugins` | `flaky` |
| `coverage.xml` | pytest `--cov` | `coverage` |
| `mutmut-junit.xml` | mutmut | `mutation` |
| live `actions/runs` API | GH Actions API | `buildHistory` |
| `dashboard/assets/history.jsonl` | trendline source | `trends` |

All four artifacts are uploaded by `ci.yml` (30-day retention; see
`.github/workflows/ci.yml` `upload-test-artifacts` and
`upload-mutation-artifacts`).

## How the page is updated

1. `ci.yml` runs the four gates as usual; the artifacts are written
   under `coverage.xml`, `junit.xml`, `flaky-tests.json`,
   `mutmut-junit.xml`.
2. A new `render-dashboard` step in `ci.yml` invokes
   `scripts/render_dashboard.py`, which:
   - parses each artifact (xml/json)
   - computes totals, slowest tests, flaky counts, coverage %, mutation
     kill rate
   - writes `dashboard/assets/data.js` with the resulting snapshot
   - commits no source changes — `data.js` is rewritten in place
3. On push to `main`, a separate `.github/workflows/dashboard.yml`
   triggers:
   - `actions/upload-pages-artifact@v3` uploads the `dashboard/`
     directory as a Pages artifact
   - `actions/deploy-pages@v4` deploys to
     `https://<owner>.github.io/software-engineering-harness/`
4. PRs do **not** trigger `dashboard.yml`; the rendered snapshot still
   appears in the GH job summary, but the live site only updates on
   merge to `main`.

## Files in this layout

```
dashboard/
  index.html              (12.7 KB)  single-page site
  assets/
    chart.umd.min.js      (208 KB)   Chart.js 4.5.1, vendored, sha256-pinned
    data.js               (2.1 KB)   rendered snapshot (window.DASHBOARD_DATA)
.github/workflows/
  ci.yml                  (+render-dashboard step)
  dashboard.yml           (Pages deploy chain)
scripts/
  render_dashboard.py     (artifact → data.js)
docs/
  engineering-dashboard.md (this file)
```

## Pinned vendor

| Asset | Version | URL of origin | sha256 |
|---|---|---|---|
| `dashboard/assets/chart.umd.min.js` | Chart.js 4.5.1 | https://unpkg.com/chart.js@4.5.1/dist/chart.umd.min.js | `48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a` |

The sha256 is asserted by `tests/unit/ci/test_g12_dashboard_workflow.py::test_dashboard_chart_sha256_pinned`.
To update Chart.js, bump the version + sha256 in the test in the same
PR that vendors the new file.

## Schema reference: `window.DASHBOARD_DATA`

The data sidecar is a single global. All fields are defensive-defaulted
to safe "no data yet" shapes so first-run and partial-failure states
render a usable page.

```js
window.DASHBOARD_DATA = {
  generatedAt: "<ISO 8601>",
  totals: { passed, failed, skipped, errors, duration, passRate, testCount },
  coverage: { percent, coveredLines, totalLines, perFile: [{ path, percent }] },
  slowest: [{ name, duration }],
  flaky: { flakyCount, brokenCount, examples: [{ nodeid, attempts, finalOutcome }] },
  mutation: { killed, survived, timeout, total, killRate, skipped },
  buildHistory: [{ sha, conclusion, runId, branch }],  // pre-loaded or fetched live
  meta: { branch, commitSha, runId, workflowUrl },
};
```

The `tests/unit/ci/test_g12_dashboard_workflow.py::test_data_js_handles_empty_artifacts_gracefully`
test round-trips a fresh `window.DASHBOARD_DATA` to confirm every key
is present.

## Why we vendor Chart.js instead of CDN

- **No third-party tracking** of dashboard viewers.
- **GH Pages rate limits** apply to CDN bandwidth; vendoring
  offloads ~208 KB from the upstream CDN to GitHub's infrastructure.
- **Offline viewing** still works (e.g., for local mirror).
- **Auditability** — a pinned sha256 in the test guards against a
  compromised CDN push.

## Trendlines

The dashboard shows **point-in-time** metrics. The trendlines panel
adds per-metric trendlines (last 30 runs) so you can spot regressions
before they ship.

### How it works

* Every dashboard.yml run on `main` curls the currently-deployed
  `dashboard/assets/history.jsonl` from the live Pages URL.
* `scripts/render_dashboard.py` parses the JSONL history (one row per
  past run), appends a new row with the current metrics, computes
  trend arrays for each metric, and writes the updated `data.js` + the
  updated `history.jsonl` to disk.
* The dashboard.yml artifact upload (path: `dashboard/`) bundles both
  files together; the new Pages deploy serves the updated bundle.

### Schema additions to `window.DASHBOARD_DATA`

```json
{
  "trends": {
    "tests":     [1400, 1420, 1432, 1450],
    "passRate":  [0.98, 0.99, 1.00, 0.99],
    "coverage":  [0.85, 0.86, 0.88, 0.88],
    "mutation":  [0.80, 0.82, 0.89, 0.89]
  }
}
```

### History row schema (JSONL, one line per run)

```json
{"ts":"2026-07-19T20:00:00Z","commitSha":"abc123...","runId":"12345","tests":1450,"passRate":0.99,"coverage":0.88,"mutation":0.89}
```

### Defensive behavior

* Missing `history.jsonl` (first deploy after a fresh repo) → start
  with empty trends; charts show "—" until 2+ rows accumulate.
* Malformed JSONL lines are skipped (not crash).
* Concurrent runs may interleave rows — tolerated; trends are sorted
  by `ts` if present, otherwise by insertion order.

### Why not commit history.jsonl to main?

Pages source is "GitHub Actions" (set in repo Settings → Pages →
Source). The dashboard.yml artifact IS the source of truth. Committing
to main would re-trigger ci.yml + dashboard.yml in a loop. The
fetch-from-live-URL pattern breaks the loop and keeps `main` clean.

## Future capabilities

- **Per-PR drilldown**: link out from each summary row to the
  corresponding PR; needs a `provenance` row in `data.js`.
- **SLO badges**: uptime %, MTBF, MTTR.
- **Per-metric sparklines beyond 30 days**: today we keep the last
  30 rows of `history.jsonl`; long-horizon charts would need a
  rolled-up monthly aggregate.
