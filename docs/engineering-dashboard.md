# Engineering dashboard (G12)

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

The dashboard reads four CI artifacts already produced by our pipeline
(G1a–G2 + G1c), plus a live fetch from the GitHub Actions REST API:

| Artifact | Source step | Sections it feeds |
|---|---|---|
| `junit.xml` | pytest (G1a) | `totals`, `slowest` |
| `flaky-tests.json` | flaky_plugin (G1c) | `flaky` |
| `coverage.xml` | pytest --cov (G1a) | `coverage` |
| `mutmut-junit.xml` | mutation-test (G2) | `mutation` |
| live `actions/runs` API | GH Actions API | `buildHistory` |

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

## Files in this slice

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

## Future slices

- **G12b**: trendlines (per-section sparkline over the last 30 days,
  sourced from a `dashboard/history.jsonl` artifact).
- **G12c**: per-PR drilldown (link out from each summary row to the
  GH Actions run).
- **G12d**: SLO badges (uptime %, MTBF, MTTR).
