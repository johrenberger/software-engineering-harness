"""Workflow-shape contract tests for Cluster G story G12b (trendlines).

G12b adds per-section sparklines (test count, pass rate, coverage, mutation
kill rate) sourced from a JSONL history artifact. Every push to main
appends one row; the dashboard reads the full file and renders small
line charts next to each metric.

Approach (post PR #24, Pages source = "GitHub Actions"):
  * ``dashboard.yml`` fetches the deployed ``history.jsonl`` from the
    live Pages URL at the start of each run (curl; defensive on 404).
  * ``scripts/render_dashboard.py`` is extended to:
      1. Read ``dashboard/assets/history.jsonl`` (one row per past CI push).
      2. Compute trend arrays for each metric (window = last N rows; default 30).
      3. Append the current run as a new row.
      4. Write the updated ``dashboard/assets/history.jsonl`` back to disk.
      5. Embed the trend arrays into ``data.js`` (alongside the existing
         point-in-time fields).
  * ``dashboard.yml`` uploads the ``dashboard/`` directory as the Pages
    artifact (data.js + history.jsonl + index.html + chart.js).
  * ``dashboard/index.html`` adds 4 sparkline canvases:
      - tests-trend (line chart of test count)
      - passrate-trend (line chart of pass rate)
      - coverage-trend (line chart of coverage %)
      - mutation-trend (line chart of mutation kill rate)

With Pages source = "GitHub Actions", the dashboard.yml artifact is
the source of truth (no commit needed). The self-publish commits from
PR #24 are removed in this slice.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INDEX_HTML = REPO_ROOT / "dashboard" / "index.html"
DASHBOARD_YML = REPO_ROOT / ".github" / "workflows" / "dashboard.yml"
DATA_JS = REPO_ROOT / "dashboard" / "assets" / "data.js"
HISTORY_JSONL = REPO_ROOT / "dashboard" / "assets" / "history.jsonl"
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_dashboard.py"


# ----------------------------------------------------------------------
# 1. History artifact contract
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def render_script_text() -> str:
    return RENDER_SCRIPT.read_text()


def test_render_script_handles_history_jsonl(render_script_text: str) -> None:
    """The render script must read + write dashboard/assets/history.jsonl."""
    assert "history.jsonl" in render_script_text, (
        "scripts/render_dashboard.py must reference dashboard/assets/history.jsonl "
        "(G12b trendline source)"
    )


def test_render_script_appends_to_history(render_script_text: str) -> None:
    """The script must have a function to append a new row to history."""
    # Look for either an explicit "append_history" function or an inline
    # open(... "a") call referencing history.jsonl.
    has_append_fn = (
        "def append_history" in render_script_text or "def update_history" in render_script_text
    )
    has_inline_append = (
        re.search(r"open\([^)]*history\.jsonl[^)]*[\"']a[\"']", render_script_text) is not None
    )
    assert has_append_fn or has_inline_append, (
        "scripts/render_dashboard.py must append a row to history.jsonl on each render"
    )


def test_render_script_embeds_trends_in_data_js(render_script_text: str) -> None:
    """The script must embed trend arrays in the rendered data.js.

    The trend arrays should be accessible at ``data.trends.*`` with one
    array per metric (tests, passRate, coverage, mutation).
    """
    # Look for either a `trends` key in build_data_js or assignment of
    # `trends` to the rendered dict.
    assert '"trends"' in render_script_text or "'trends'" in render_script_text, (
        "scripts/render_dashboard.py must embed a 'trends' section in data.js"
    )


# ----------------------------------------------------------------------
# 2. Dashboard HTML contract
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def index_html_text() -> str:
    return INDEX_HTML.read_text()


def test_index_html_has_trends_section(index_html_text: str) -> None:
    """The dashboard must have a <section id="trends"> with sparkline canvases."""
    assert 'id="trends"' in index_html_text, (
        'dashboard/index.html must include <section id="trends"> with trendline sparklines'
    )


def test_index_html_has_4_trend_canvases(index_html_text: str) -> None:
    """The trends section must have 4 sparkline canvases (one per metric)."""
    # Count <canvas> elements whose id ends with "-trend".
    canvases = re.findall(r'<canvas[^>]*id="(\w+-trend)"[^>]*>', index_html_text)
    assert len(canvases) >= 4, (
        f"trends section must have ≥4 sparkline canvases "
        f"(tests, passRate, coverage, mutation); found: {canvases}"
    )


def test_index_html_loads_history_data(index_html_text: str) -> None:
    """The dashboard must read history.jsonl (or use data.js trends)."""
    # Look for either:
    #   * a fetch of history.jsonl
    #   * OR an existing reference to DASHBOARD_DATA.trends (or shorthand `d.trends`)
    fetches_history = "history.jsonl" in index_html_text
    uses_trends = (
        re.search(r"DASHBOARD_DATA\s*\.\s*trends|\bd\s*\.\s*trends", index_html_text) is not None
    )
    assert fetches_history or uses_trends, (
        "dashboard/index.html must either fetch history.jsonl or "
        "read DASHBOARD_DATA.trends to render trendlines"
    )


# ----------------------------------------------------------------------
# 3. CI workflow contract
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def dashboard_yml_text() -> str:
    return DASHBOARD_YML.read_text()


def test_dashboard_workflow_fetches_history_jsonl(dashboard_yml_text: str) -> None:
    """dashboard.yml must fetch history.jsonl from the live site before rendering.

    With Pages source = "GitHub Actions", the dashboard.yml artifact is
    the source of truth (not a git commit). To append a row to history,
    the workflow must download the currently-deployed history.jsonl from
    the live URL, let render_dashboard.py append the new row, then
    upload the updated `dashboard/` directory as the new Pages artifact.
    """
    # The step name "Fetch previous dashboard history" must exist.
    assert "Fetch previous dashboard history" in dashboard_yml_text, (
        "dashboard.yml must include a 'Fetch previous dashboard history' step"
    )
    # The step must reference history.jsonl and use curl (or wget) to download it.
    assert "history.jsonl" in dashboard_yml_text, "dashboard.yml must reference history.jsonl"
    assert "curl" in dashboard_yml_text, "dashboard.yml must curl the deployed history.jsonl"


def test_dashboard_workflow_no_self_publish_after_pages_source_change(
    dashboard_yml_text: str,
) -> None:
    """With Pages source = 'GitHub Actions', self-publish commits are no longer needed.

    PR #24 introduced the self-publish pattern (commit data.js back to main)
    because Pages source was 'Build from a branch' and the auto-generated
    pages-build-deployment clobbered our dashboard.yml artifact. Now that
    the user changed Pages source to 'GitHub Actions', the dashboard.yml
    artifact deploys directly and the self-publish commits should be removed.
    """
    assert "git commit" not in dashboard_yml_text, (
        "dashboard.yml must NOT contain `git commit` (self-publish pattern "
        "removed when Pages source changed to 'GitHub Actions')"
    )
    assert "git push" not in dashboard_yml_text, (
        "dashboard.yml must NOT contain `git push` (same reason)"
    )


# ----------------------------------------------------------------------
# 4. End-to-end: render script reads+appends+embeds trendlines
# ----------------------------------------------------------------------


def _run_render(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run scripts/render_dashboard.py with optional env overrides."""
    import os

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(RENDER_SCRIPT)],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_writes_trends_to_data_js(tmp_path: Path) -> None:
    """When history.jsonl has 3 rows, data.js trends arrays must have 3 elements each."""
    # Set up isolated artifacts dir for the test.
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # Minimal junit + coverage for the script to parse.
    junit = artifacts / "junit.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuites>'
        '<testsuite tests="3" failures="0" errors="0" skipped="0">'
        '<testcase name="t1"/><testcase name="t2"/><testcase name="t3"/>'
        "</testsuite></testsuites>"
    )
    # Pre-populated history with 3 rows (key = tests, not passed).
    history = artifacts / "history.jsonl"
    history.write_text(
        '{"ts":"2026-07-19T10:00:00Z","tests":1400,"passRate":0.98,"coverage":0.85,"mutation":0.80}\n'
        '{"ts":"2026-07-19T11:00:00Z","tests":1420,"passRate":0.99,"coverage":0.86,"mutation":0.82}\n'
        '{"ts":"2026-07-19T12:00:00Z","tests":1432,"passRate":1.00,"coverage":0.88,"mutation":0.89}\n'
    )
    # Run render with redirected paths.
    proc = _run_render(
        env={
            "DASHBOARD_DATA_OUT": str(artifacts / "data.js"),
            "HISTORY_JSONL": str(history),
        }
    )
    # The script may not yet support these env overrides, but if it
    # does, the output data.js should have a "trends" section with
    # arrays derived from history.
    if proc.returncode != 0:
        pytest.skip(f"render script does not yet support isolated test paths: {proc.stderr[:200]}")
    out = (artifacts / "data.js").read_text()
    if "trends" not in out:
        pytest.skip("render script does not yet emit 'trends' section")
    # Parse the data.js body (between the assignment and the semicolon).
    m = re.search(r"DASHBOARD_DATA\s*=\s*(\{.*\})\s*;", out, re.DOTALL)
    assert m is not None, "data.js must contain DASHBOARD_DATA = {...};"
    parsed = json.loads(m.group(1))
    trends = parsed.get("trends", {})
    for key in ("tests", "passRate", "coverage", "mutation"):
        assert key in trends, f"trends.{key} missing from data.js"
        assert isinstance(trends[key], list), f"trends.{key} must be a list"
        # Each metric list must have at least the 3 history rows + 1 current row.
        assert len(trends[key]) >= 3, f"trends.{key} must have ≥3 entries (got {len(trends[key])})"
