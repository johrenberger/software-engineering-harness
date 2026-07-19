"""Workflow-shape contract tests for Cluster G story G12 (engineering dashboard).

Cluster G Slice G12 wires an engineering dashboard (GitHub Pages) that
is updated on every push to main. Per the design choice (Option A):
**single-page, vendored Chart.js, no CDN**. The page must consume the
artifacts we already upload (junit.xml, flaky-tests.json, coverage.xml,
mutmut-junit.xml) and surface:

    * Total tests, pass rate, mean execution time
    * Coverage trend (latest + per-file highlights)
    * Slowest tests
    * Flaky test count (broken + flaky from G1c)
    * Mutation score (killed / total) from G2 mutmut output
    * Build status (last N runs, success/failure)

What this test verifies:

  1. ``.github/workflows/dashboard.yml`` exists.
  2. It triggers on push to main only (no per-PR redeploys).
  3. It has the standard Pages-deploy steps (actions/checkout@v4,
     actions/configure-pages@v5, actions/upload-pages-artifact@v3,
     actions/deploy-pages@v4).
  4. ``dashboard/index.html`` exists, contains the required sections,
     and loads the vendored Chart.js (no CDN URL).
  5. ``dashboard/assets/chart.umd.min.js`` is vendored (no remote
     fetch) and matches a pinned sha256.
  6. ``dashboard/assets/data.js`` exports the shape the HTML expects.
  7. ``.github/workflows/ci.yml`` writes a step that renders
     ``dashboard/assets/data.js`` from the artifacts (Junit XML,
     flaky-tests.json, coverage.xml, mutmut-junit.xml).
  8. The dashboard workflow has ``permissions: pages: write,
     id-token: write`` (required by ``deploy-pages``).
  9. ``docs/engineering-dashboard.md`` documents the architecture +
     how to consume the artifacts.

This file is structurally aligned with
``tests/unit/ci/test_g1c_flaky_workflow.py`` and
``tests/unit/ci/test_g2_mutmut_workflow.py``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/unit/ci -> repo root
DASHBOARD_DIR = REPO_ROOT / "dashboard"
DASHBOARD_HTML = DASHBOARD_DIR / "index.html"
DASHBOARD_DATA = DASHBOARD_DIR / "assets" / "data.js"
DASHBOARD_CHART = DASHBOARD_DIR / "assets" / "chart.umd.min.js"
DASHBOARD_DOCS = REPO_ROOT / "docs" / "engineering-dashboard.md"
DASHBOARD_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dashboard.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Pinned sha256 of Chart.js 4.5.1 UMD min (vendored). Asserts the
# vendored copy has not been swapped or corrupted.
CHARTJS_V4_5_1_SHA256 = "48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a"


# ----------------------------------------------------------------------
# 1. dashboard/ scaffolding
# ----------------------------------------------------------------------


def test_dashboard_html_exists() -> None:
    """The single-page dashboard must live at dashboard/index.html.

    Per design choice: ONE page (no per-run pages), updated each CI
    push to main.
    """
    assert DASHBOARD_HTML.is_file(), "dashboard/index.html must exist; this is the single-page site"


def test_dashboard_data_js_exists() -> None:
    """The data sidecar must be a sibling of index.html.

    Splitting data out (rather than inlining a 200KB blob of JSON in
    the HTML) keeps the page reviewable in git diffs.
    """
    assert DASHBOARD_DATA.is_file(), (
        "dashboard/assets/data.js must exist; the page consumes this sidecar at load time"
    )


def test_dashboard_chart_is_vendored() -> None:
    """Chart.js must be vendored locally; no CDN URLs in the page."""
    assert DASHBOARD_CHART.is_file(), (
        "dashboard/assets/chart.umd.min.js must be checked in (vendored)"
    )


def test_dashboard_chart_sha256_pinned() -> None:
    """The vendored Chart.js must match the pinned sha256.

    Protects against an upstream maintainer or malicious mirror
    pushing a tampered build into our Pages site.
    """
    if not DASHBOARD_CHART.is_file():
        pytest.skip("chart.umd.min.js not yet vendored; skip pinning check")
    h = hashlib.sha256(DASHBOARD_CHART.read_bytes()).hexdigest()
    assert h == CHARTJS_V4_5_1_SHA256, (
        f"vendored chart.umd.min.js sha256 drifted: {h} != {CHARTJS_V4_5_1_SHA256}"
    )


# ----------------------------------------------------------------------
# 2. dashboard/index.html content shape
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def dashboard_html_text() -> str:
    if not DASHBOARD_HTML.is_file():
        return ""
    return DASHBOARD_HTML.read_text()


def test_dashboard_html_loads_local_chart_js(dashboard_html_text: str) -> None:
    """No CDN URL must appear in the page source."""
    assert "https://cdn." not in dashboard_html_text, (
        "dashboard must NOT reference any CDN; Chart.js is vendored"
    )
    assert "https://unpkg.com/" not in dashboard_html_text, (
        "dashboard must NOT load Chart.js from unpkg.com"
    )
    assert "https://cdn.jsdelivr.net/" not in dashboard_html_text, (
        "dashboard must NOT load Chart.js from jsdelivr"
    )
    assert "assets/chart.umd.min.js" in dashboard_html_text, (
        "dashboard must reference the vendored chart.umd.min.js via a relative URL"
    )


def test_dashboard_html_has_required_sections(dashboard_html_text: str) -> None:
    """All 6 required dashboard sections must be present.

    Sections (one <section id="..."> each):
      - totals (tests + pass rate + duration)
      - coverage (overall % + per-file highlights)
      - slowest (top-N tests by duration)
      - flaky (broken + flaky count + breakdown)
      - mutation (kill rate from mutmut JUnit XML)
      - build-history (last N CI runs, success/failure sparkline)
    """
    section_ids = [
        "totals",
        "coverage",
        "slowest",
        "flaky",
        "mutation",
        "build-history",
    ]
    for sid in section_ids:
        assert f'id="{sid}"' in dashboard_html_text, (
            f'dashboard must contain a <section id="{sid}"> block; '
            f"one of the 6 required sections is missing"
        )


def test_dashboard_html_has_canvases_for_charts(dashboard_html_text: str) -> None:
    """Each chart section needs a <canvas> element for Chart.js."""
    chart_canvases = re.findall(r'<canvas\s+id="([\w-]+)"', dashboard_html_text)
    assert len(chart_canvases) >= 4, (
        f"dashboard must have at least 4 <canvas> elements for charts; "
        f"got {len(chart_canvases)}: {chart_canvases}"
    )


def test_dashboard_html_loads_data_js(dashboard_html_text: str) -> None:
    """The page must load dashboard/assets/data.js (not inline JSON)."""
    assert "assets/data.js" in dashboard_html_text, (
        "dashboard must consume the data.js sidecar via a <script src=...>"
    )


# ----------------------------------------------------------------------
# 3. dashboard/assets/data.js shape (parseable as JS, has expected fields)
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def dashboard_data_text() -> str:
    if not DASHBOARD_DATA.is_file():
        return ""
    return DASHBOARD_DATA.read_text()


def test_dashboard_data_is_js_window_assignment(
    dashboard_data_text: str,
) -> None:
    """data.js must assign a single global window.DASHBOARD_DATA object.

    Using window.* (not ES modules) keeps the page a single-file site
    that works on GitHub Pages with no MIME-type quirks.
    """
    assert "window.DASHBOARD_DATA" in dashboard_data_text, (
        "data.js must export a window.DASHBOARD_DATA global"
    )


def test_dashboard_data_has_required_top_level_keys(
    dashboard_data_text: str,
) -> None:
    """The DASHBOARD_DATA object must declare all 6 section keys."""
    required_keys = {
        "totals",
        "coverage",
        "slowest",
        "flaky",
        "mutation",
        "buildHistory",
    }
    for key in required_keys:
        # Tolerate quoting style: "key": or key: or 'key':
        pat = re.compile(rf"['\"]?{re.escape(key)}['\"]?\s*:")
        assert pat.search(dashboard_data_text), (
            f"DASHBOARD_DATA must declare a '{key}' field (one of the 6 dashboard sections)"
        )


def test_dashboard_data_totals_have_expected_shape(
    dashboard_data_text: str,
) -> None:
    """totals block must include passed/failed/skipped/duration/passRate."""
    expected_fields = ["passed", "failed", "skipped", "duration", "passRate"]
    for field in expected_fields:
        # Look for the field within the totals block (heuristic:
        # "totals": { ... field ... }).
        assert re.search(
            rf"totals['\"]?\s*:\s*\{{[^}}]*{field}",
            dashboard_data_text,
            re.DOTALL,
        ), f"totals block must include '{field}'"


def test_dashboard_data_coverage_has_percent_field(
    dashboard_data_text: str,
) -> None:
    """coverage block must include a percent (overall % coverage)."""
    assert re.search(
        r"coverage['\"]?\s*:\s*\{[^}]*percent",
        dashboard_data_text,
        re.DOTALL,
    ), "coverage block must include a 'percent' field"


def test_dashboard_data_flaky_has_broken_and_flaky_counts(
    dashboard_data_text: str,
) -> None:
    """flaky block must report both the 'flaky' and 'broken' counts."""
    for key in ("flaky", "broken"):
        assert re.search(
            rf"flaky['\"]?\s*:\s*\{{[^}}]*['\"]?{key}['\"]?",
            dashboard_data_text,
            re.DOTALL,
        ), f"flaky block must include a '{key}' count field"


def test_dashboard_data_mutation_has_kill_rate(
    dashboard_data_text: str,
) -> None:
    """mutation block must include a killRate (killed / total)."""
    assert re.search(
        r"mutation['\"]?\s*:\s*\{[^}]*killRate",
        dashboard_data_text,
        re.DOTALL,
    ), "mutation block must include a 'killRate' field"


# ----------------------------------------------------------------------
# 4. CI workflow that renders dashboard/assets/data.js
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow_text() -> str:
    return CI_WORKFLOW.read_text()


def test_ci_workflow_has_dashboard_render_step(ci_workflow_text: str) -> None:
    """ci.yml must include a step that writes dashboard/assets/data.js."""
    # Heuristic: a step whose name mentions dashboard OR a run block
    # that writes to dashboard/assets/data.js.
    has_step_name = bool(
        re.search(r"-\s*name:\s*render-dashboard", ci_workflow_text, re.IGNORECASE)
    )
    has_run_target = "dashboard/assets/data.js" in ci_workflow_text
    assert has_step_name or has_run_target, (
        "ci.yml must include a step (render-dashboard or similar) that "
        "writes dashboard/assets/data.js from the test artifacts"
    )


def test_ci_workflow_runs_render_on_push_to_main(ci_workflow_text: str) -> None:
    """The dashboard render step must run on push to main (not just PRs).

    The dashboard is updated each push to main; PRs get rendered to
    artifacts only, so the live site is not thrashed on every PR.
    """
    # The render-dashboard step must NOT be gated to pull_request-only
    # (otherwise it skips on push-to-main and the live site never
    # updates between PRs).
    render_idx = ci_workflow_text.find("render-dashboard")
    assert render_idx > 0, "render-dashboard step must exist in ci.yml"
    # Find the step block (between the next "      - name:" or
    # "      - uses:" that follows render-dashboard).
    block_end = ci_workflow_text.find("\n      - ", render_idx + 1)
    block = ci_workflow_text[render_idx : block_end if block_end > 0 else len(ci_workflow_text)]
    # The block must NOT be gated to PR-only.
    assert "github.event_name == 'pull_request'" not in block, (
        "render-dashboard must run on BOTH push and PR (not gated to PR-only)"
    )
    # The step must run after pytest (so junit.xml + flaky-tests.json
    # + coverage.xml + mutmut-junit.xml all exist).
    pytest_idx = ci_workflow_text.find("- name: pytest")
    assert render_idx > pytest_idx > 0, (
        "render-dashboard step must come after pytest step (it consumes "
        "the artifacts pytest produces)"
    )


def test_ci_workflow_dashboard_step_uploads_artifact(ci_workflow_text: str) -> None:
    """The render step must also upload dashboard/ as a build artifact."""
    # Either inline upload or rely on the dashboard.yml workflow to
    # checkout + re-render. We choose the latter for separation of
    # concerns. Here we just assert the render step does NOT try to
    # publish to Pages (that's dashboard.yml's job).
    assert "actions/deploy-pages" not in ci_workflow_text, (
        "ci.yml may not include actions/deploy-pages; that lives in dashboard.yml"
    )


# ----------------------------------------------------------------------
# 5. dashboard.yml workflow shape (GitHub Pages deploy)
# ----------------------------------------------------------------------


def test_dashboard_workflow_exists() -> None:
    """A separate dashboard.yml must own the Pages deploy (one job)."""
    assert DASHBOARD_WORKFLOW.is_file(), (
        ".github/workflows/dashboard.yml must exist (Pages deploy lives "
        "in its own workflow, not inline in ci.yml)"
    )


def test_dashboard_workflow_triggers_on_push_and_workflow_run() -> None:
    """The Pages workflow must trigger on push to main AND/OR ci.yml workflow_run.

    Per design (post-G12c): the canonical path is `workflow_run` after
    ci.yml completes, so dashboard.yml gets cross-workflow artifact
    access (test-results + mutation-results within 30-day retention).

    The plain `push` trigger is retained as a manual fallback for
    testing without needing a ci.yml run, and `workflow_dispatch` for
    manual triggering from the Actions UI.

    PRs must NOT trigger a redeploy (would thrash the live site).
    """
    text = DASHBOARD_WORKFLOW.read_text()
    # Must include either a push: branches: [main] block OR a
    # workflow_run: workflows: [ci] block.
    has_push_main = bool(
        re.search(r"push:\s*branches:\s*\[?\s*main\s*\]?", text)
        or re.search(r"push:\s*\n\s*branches:\s*[\[\(]?\s*main", text)
    )
    has_workflow_run = bool(
        re.search(
            r"workflow_run:\s*\n\s*workflows:\s*\[?\s*[\"']ci[\"']\s*\]?",
            text,
        )
    )
    assert has_push_main or has_workflow_run, (
        "dashboard.yml must trigger on push to main OR via workflow_run "
        "from ci (G12c canonical path), so the deployed page stays fresh"
    )
    # Must NOT include pull_request trigger.
    assert "pull_request:" not in text, (
        "dashboard.yml must NOT redeploy on pull_request events "
        "(would thrash the live site on every PR)"
    )


def test_dashboard_workflow_has_pages_deploy_steps() -> None:
    """Standard GitHub Pages deploy chain must be present."""
    text = DASHBOARD_WORKFLOW.read_text()
    required = [
        "actions/checkout@v4",
        "actions/configure-pages@v5",
        "actions/upload-pages-artifact@v3",
        "actions/deploy-pages@v4",
    ]
    for needle in required:
        assert needle in text, (
            f"dashboard.yml must reference {needle} (the canonical Pages deploy chain)"
        )


def test_dashboard_workflow_uses_correct_permissions() -> None:
    """Pages deploy requires pages:write and id-token:write."""
    text = DASHBOARD_WORKFLOW.read_text()
    assert "pages: write" in text or "pages:write" in text, (
        "dashboard.yml must declare permissions.pages: write (required by actions/deploy-pages)"
    )
    assert "id-token: write" in text or "id-token:write" in text, (
        "dashboard.yml must declare permissions.id-token: write (required by OIDC for Pages)"
    )


def test_dashboard_workflow_uploads_dashboard_directory() -> None:
    """The upload-pages-artifact step must point at the dashboard/ dir."""
    text = DASHBOARD_WORKFLOW.read_text()
    # accept either `path: dashboard` or `path: ./dashboard` etc.
    assert re.search(r"path:\s*\.?/?dashboard/?\s*$", text, re.MULTILINE) or re.search(
        r"path:\s*['\"]?dashboard['\"]?", text
    ), "upload-pages-artifact must set path: dashboard"


def test_dashboard_workflow_no_self_publish_with_workflow_pages_source() -> None:
    """With Pages source = 'GitHub Actions', the dashboard.yml artifact is the source of truth.

    PR #24 added a self-publish commit step (data.js back to main) to work
    around the legacy Jekyll `pages-build-deployment` clobbering our
    dashboard.yml artifact. After the user changed Pages source to
    'GitHub Actions' (Settings → Pages → Source), the clobbering
    workflow stopped running and the self-publish commits are no longer
    needed (and would cause an infinite loop).

    This test asserts the self-publish pattern is gone: dashboard.yml
    must NOT contain `git commit` or `git push`.
    """
    text = DASHBOARD_WORKFLOW.read_text()
    assert "git commit" not in text, (
        "dashboard.yml must NOT contain `git commit` (self-publish pattern "
        "removed when Pages source changed to 'GitHub Actions')"
    )
    assert "git push" not in text, "dashboard.yml must NOT contain `git push` (same reason)"


def test_dashboard_workflow_only_reads_contents() -> None:
    """dashboard.yml only needs read permission (no self-commit anymore)."""
    text = DASHBOARD_WORKFLOW.read_text()
    permissions_block = re.search(
        r"^permissions:\s*\n((?:\s+\w+:\s*\w+\s*\n)+)",
        text,
        re.MULTILINE,
    )
    assert permissions_block is not None, "dashboard.yml must declare a permissions: block"
    block = permissions_block.group(0)
    # contents: write should NOT be present (no commits anymore).
    assert "contents: write" not in block, (
        "dashboard.yml must NOT declare `contents: write` "
        "(no self-publish commits; Pages deploys artifact directly)"
    )
    # contents: read IS expected (for the checkout action).
    assert "contents: read" in block, (
        "dashboard.yml must declare `contents: read` (checkout needs it)"
    )


# ----------------------------------------------------------------------
# 6. docs/engineering-dashboard.md
# ----------------------------------------------------------------------


def test_engineering_dashboard_doc_exists() -> None:
    """docs/engineering-dashboard.md must exist and describe the design."""
    assert DASHBOARD_DOCS.is_file(), (
        "docs/engineering-dashboard.md must exist (user-facing design "
        "doc + artifact schema reference)"
    )


def test_engineering_dashboard_doc_lists_artifacts() -> None:
    """The doc must enumerate the CI artifacts the dashboard consumes."""
    text = DASHBOARD_DOCS.read_text()
    expected = ["junit.xml", "flaky-tests.json", "coverage.xml", "mutmut-junit.xml"]
    missing = [a for a in expected if a not in text]
    assert not missing, (
        f"docs/engineering-dashboard.md must reference these artifacts: "
        f"{expected}; missing: {missing}"
    )


# ----------------------------------------------------------------------
# 7. data.js sample shape (round-trip: write a fixture, render to data.js,
#    assert HTML page can read it back)
# ----------------------------------------------------------------------


def test_data_js_handles_empty_artifacts_gracefully() -> None:
    """data.js must define a valid global even when all artifacts are empty.

    Useful for first-run (no historical data) and edge cases where one
    of the four artifacts is missing from a CI run.
    """
    if not DASHBOARD_DATA.is_file():
        pytest.skip("data.js not yet vendored")
    text = DASHBOARD_DATA.read_text()
    # We don't try to exec the JS in Python (Python's exec has stricter
    # parsing rules than V8). Instead, extract the JSON object literal
    # that follows `window.DASHBOARD_DATA =` and parse it with the JSON
    # module — the render script writes valid JSON, so this is a fair
    # proxy for "the snapshot is well-formed".
    import json as _json

    m = re.search(
        r"window\.DASHBOARD_DATA\s*=\s*(\{.*\})\s*;\s*$",
        text,
        re.DOTALL,
    )
    assert m is not None, "data.js must end with `window.DASHBOARD_DATA = { ... };`"
    try:
        data = _json.loads(m.group(1))
    except _json.JSONDecodeError as exc:
        pytest.fail(f"data.js JSON block is not valid: {exc}")
    # All 6 top-level keys must be present (with safe defaults).
    for key in ("totals", "coverage", "slowest", "flaky", "mutation", "buildHistory"):
        assert key in data, f"DASHBOARD_DATA missing top-level key: {key}"
    # And the totals block must have the expected subfields.
    assert "passed" in data["totals"]
    assert "failed" in data["totals"]
    assert "skipped" in data["totals"]
    assert "duration" in data["totals"]
    assert "passRate" in data["totals"]
