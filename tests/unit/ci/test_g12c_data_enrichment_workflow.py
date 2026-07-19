"""Contract tests for G12c — Dashboard data enrichment.

G12c enables the live dashboard to show REAL test results (not placeholder
zeros) by sharing artifacts between ci.yml (producer) and dashboard.yml
(consumer). Before G12c the dashboard's coverage / mutation / flaky
blocks were always 0 because dashboard.yml couldn't access the artifacts
ci.yml produced.

Verification strategy:
1. Static checks on .github/workflows/ci.yml + dashboard.yml shape.
2. End-to-end render: place fake junit.xml + coverage.xml +
   flaky-tests.json + mutmut-junit.xml in a temp repo and run
   render_dashboard.py; assert coverage % is non-zero.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

CI_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"
DASHBOARD_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "dashboard.yml"
SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


# ----------------------------------------------------------------------
# 1. ci.yml shape: coverage.xml now in upload-test-artifacts
# ----------------------------------------------------------------------


def test_ci_workflow_uploads_coverage_xml_in_test_results_artifact() -> None:
    """ci.yml must upload coverage.xml alongside junit + flaky tests.

    G12c requires this so dashboard.yml's workflow_run trigger can pull
    test-results from ci.yml and populate the live `coverage` block.
    Before G12c, coverage.xml was produced but discarded — the dashboard
    always showed 0 coverage for everyone.
    """
    text = CI_WORKFLOW.read_text()
    # Find the upload-test-artifacts block.
    m = re.search(
        r"name:\s*upload-test-artifacts.*?path:\s*\|\s*\n((?:\s+[^\n]+\n)+)",
        text,
        re.DOTALL,
    )
    assert m is not None, (
        "ci.yml must contain an upload-test-artifacts step with a `path: |` "
        "block listing the artifacts to upload"
    )
    block = m.group(1)
    # Must list coverage.xml in this upload.
    assert "coverage.xml" in block, (
        "upload-test-artifacts must include coverage.xml in its `path:` list "
        "(G12c) so dashboard.yml can fetch it via workflow_run artifact download"
    )


# ----------------------------------------------------------------------
# 2. dashboard.yml shape: workflow_run trigger + actions:read + downloads
# ----------------------------------------------------------------------


def test_dashboard_workflow_triggers_on_ci_workflow_run() -> None:
    """dashboard.yml must trigger via workflow_run on ci.yml completion.

    workflow_run is the canonical trigger that gives dashboard.yml access
    to ci.yml's artifacts within the same run-id scope (30-day retention).
    """
    text = DASHBOARD_WORKFLOW.read_text()
    # Must contain a workflow_run block referencing the ci workflow.
    has_workflow_run = bool(
        re.search(
            r"workflow_run:\s*\n\s*workflows:\s*\[?\s*[\"']ci[\"']\s*\]?",
            text,
        )
    )
    assert has_workflow_run, (
        "dashboard.yml must contain a workflow_run trigger for the ci workflow "
        "(G12c) so cross-workflow artifact download via run-id works"
    )


def test_dashboard_workflow_has_actions_read_permission() -> None:
    """dashboard.yml needs actions: read for cross-workflow artifact download."""
    text = DASHBOARD_WORKFLOW.read_text()
    assert "actions: read" in text or "actions:read" in text, (
        "dashboard.yml must declare `actions: read` permission "
        "(required by actions/download-artifact@v4 with run-id for "
        "cross-workflow artifact sharing)"
    )


def test_dashboard_workflow_downloads_test_results_artifact() -> None:
    """dashboard.yml must download test-results from ci.yml via run-id."""
    text = DASHBOARD_WORKFLOW.read_text()
    # G4: actions are SHA-pinned. The action is `actions/download-artifact`
    # at any version; G4 contract tests pin the exact SHA.
    download_block = re.search(
        r"uses:\s*actions/download-artifact@\S+.*?\n\s*with:\s*\n"
        r"((?:\s+\w+:[^\n]*\n)+)",
        text,
    )
    assert download_block is not None, (
        "dashboard.yml must use actions/download-artifact@<sha> (G4 pinned) "
        "to fetch ci.yml's artifacts"
    )
    # At least one download must reference test-results.
    assert re.search(
        r"name:\s*test-results",
        "\n".join(re.findall(r"with:\s*\n((?:\s+\w+:[^\n]*\n)+)", text)),
    ), "dashboard.yml must download the `test-results` artifact from ci.yml"
    # run-id must reference the workflow_run id.
    assert "run-id:" in text, (
        "dashboard.yml's download-artifact calls must specify `run-id` "
        "(G12c: required for cross-workflow artifact download)"
    )


def test_dashboard_workflow_downloads_mutation_results_artifact() -> None:
    """dashboard.yml must download mutation-results too (defensively)."""
    text = DASHBOARD_WORKFLOW.read_text()
    assert "name: mutation-results" in text, (
        "dashboard.yml must download the `mutation-results` artifact "
        "from ci.yml (defensive on missing file via continue-on-error)"
    )


def test_dashboard_workflow_stages_artifacts_for_render() -> None:
    """dashboard.yml must copy artifacts to the names render_dashboard.py expects.

    render_dashboard.py reads from `junit.xml`, `flaky-tests.json`,
    `coverage.xml`, `mutmut-junit.xml` in the repo root by default.
    dashboard.yml downloads go to a subdirectory; they must be copied
    to repo-root paths before render_dashboard.py is invoked.
    """
    text = DASHBOARD_WORKFLOW.read_text()
    # Find a step that does `cp junit.xml` (the staging step).
    assert re.search(
        r"cp\s+\S*/junit\.xml\s+junit\.xml",
        text,
    ), (
        "dashboard.yml must stage ci.yml's junit.xml to the repo root "
        "(render_dashboard.py's default input path)"
    )
    assert "coverage.xml" in text and re.search(
        r"cp\s+\S*/coverage\.xml\s+coverage\.xml",
        text,
    ), "dashboard.yml must stage ci.yml's coverage.xml to the repo root"


def test_dashboard_workflow_history_url_points_at_site_root() -> None:
    """The history.jsonl fetch URL must be at the site root, not /dashboard/.

    The dashboard.yml artifact serves at the site root because
    actions/upload-pages-artifact@v3 strips the source directory.
    The history.jsonl file therefore lives at /assets/history.jsonl,
    not /dashboard/assets/history.jsonl.
    """
    text = DASHBOARD_WORKFLOW.read_text()
    # Find the curl line for history.jsonl (line may be wrapped over multiple lines)
    m = re.search(r"curl[\s\S]{0,300}history\.jsonl", text)
    assert m is not None, "dashboard.yml must curl the deployed history.jsonl"
    curl_line = m.group(0)
    # Must NOT contain /dashboard/ prefix.
    assert "/dashboard/" not in curl_line, (
        f"history.jsonl URL must NOT include /dashboard/ prefix "
        f"(artifact is served at site root). Got: {curl_line}"
    )


# ----------------------------------------------------------------------
# 3. Render script: end-to-end with real-looking artifacts
# ----------------------------------------------------------------------


@pytest.fixture
def fake_artifacts_dir(tmp_path: Path) -> Path:
    """Create fake junit + coverage + flaky + mutmut artifacts in tmp_path."""
    artifacts = tmp_path
    # junit.xml with 10 passed, 1 failed
    junit = textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuite name="pytest" tests="11" failures="1" errors="0" skipped="0" time="3.5">
          <testcase classname="t.A" name="test_1" time="0.1"/>
          <testcase classname="t.A" name="test_2" time="0.2"/>
          <testcase classname="t.A" name="test_3" time="0.3"/>
          <testcase classname="t.A" name="test_4" time="0.4"/>
          <testcase classname="t.A" name="test_5" time="0.5"/>
          <testcase classname="t.A" name="test_6" time="0.6"/>
          <testcase classname="t.A" name="test_7" time="0.7"/>
          <testcase classname="t.A" name="test_8" time="0.8"/>
          <testcase classname="t.A" name="test_9" time="0.9"/>
          <testcase classname="t.A" name="test_10" time="1.0"/>
          <testcase classname="t.B" name="test_fail" time="0.05">
            <failure message="boom">traceback</failure>
          </testcase>
        </testsuite>
    """)
    (artifacts / "junit.xml").write_text(junit)

    # coverage.xml with 80% line-rate (line-rate="0.80")
    coverage = textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <coverage version="7.0" timestamp="" lines-valid="100"
            lines-covered="80" line-rate="0.80"
            branches-covered="0" branches-valid="0" branch-rate="0"
            complexity="0">
          <sources><source>.</source></sources>
        </coverage>
    """)
    (artifacts / "coverage.xml").write_text(coverage)

    # flaky-tests.json: 2 flaky, 1 broken
    flaky = '{"flaky": [{"test": "a", "flaky_runs": 3}], "broken": [{"test": "b"}]}'
    (artifacts / "flaky-tests.json").write_text(flaky)

    # mutmut-junit.xml: 4 killed, 1 survived
    mutmut = textwrap.dedent("""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuite name="mutmut" tests="5" failures="1" errors="0" skipped="0">
          <testcase classname="mutmut" name="m1"><failure/></testcase>
          <testcase classname="mutmut" name="m2"/>
          <testcase classname="mutmut" name="m3"/>
          <testcase classname="mutmut" name="m4"/>
          <testcase classname="mutmut" name="m5"/>
        </testsuite>
    """)
    (artifacts / "mutmut-junit.xml").write_text(mutmut)

    return artifacts


def test_render_with_real_artifacts_populates_coverage(
    fake_artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """render_dashboard.py with real artifacts must produce non-zero coverage.

    This is the headline G12c win: live coverage on the dashboard, not 0.
    """
    # Copy artifacts to repo root (mirror what dashboard.yml's Stage step does)
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo)
    for name in ("junit.xml", "coverage.xml", "flaky-tests.json", "mutmut-junit.xml"):
        src = fake_artifacts_dir / name
        (repo / name).write_text(src.read_text())

    # Redirect dashboard output to tmp_path so we don't pollute repo
    out_path = tmp_path / "data.js"
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setenv("DASHBOARD_DATA_OUT", str(out_path))
    monkeypatch.setenv("HISTORY_JSONL", str(history_path))

    # Run the render script
    import subprocess

    result = subprocess.run(
        ["python", str(SCRIPTS_DIR / "render_dashboard.py")],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    assert result.returncode == 0, (
        f"render_dashboard.py failed: stderr={result.stderr}\nstdout={result.stdout}"
    )

    # Parse the produced data.js and assert non-zero coverage
    import json as _json
    import re as _re

    body = out_path.read_text()
    m = _re.search(r"window\.DASHBOARD_DATA\s*=\s*(\{.*\})\s*;\s*$", body, _re.DOTALL)
    assert m is not None, "rendered data.js must define window.DASHBOARD_DATA"
    data = _json.loads(m.group(1))

    # Coverage: line-rate was 0.80
    assert data["coverage"]["percent"] == pytest.approx(0.80), (
        f"coverage.percent must reflect the fake coverage.xml (got {data['coverage']})"
    )
    # Totals: 11 tests, 10 passed, 1 failed, ~91% pass rate
    assert data["totals"]["testCount"] == 11, f"totals.testCount must be 11 (got {data['totals']})"
    assert data["totals"]["failed"] == 1, f"totals.failed must be 1 (got {data['totals']})"
    # Flaky: 2 flaky + 1 broken
    assert data["flaky"]["flakyCount"] == 1, f"flaky.flakyCount must be 1 (got {data['flaky']})"
    assert data["flaky"]["brokenCount"] == 1, f"flaky.brokenCount must be 1 (got {data['flaky']})"
    # Mutation: 5 total, 1 survived
    assert data["mutation"]["total"] == 5, f"mutation.total must be 5 (got {data['mutation']})"
    assert data["mutation"]["survived"] == 1, (
        f"mutation.survived must be 1 (got {data['mutation']})"
    )

    # Clean up the artifacts we staged at the repo root.
    for name in ("junit.xml", "coverage.xml", "flaky-tests.json", "mutmut-junit.xml"):
        f = repo / name
        if f.is_file():
            f.unlink()
