"""Integration tests for scripts/render_dashboard.py.

Runs the script in the real repo with synthetic junit.xml +
flaky-tests.json + coverage.xml + mutmut-junit.xml placed at the
repo root, then asserts dashboard/assets/data.js was written
correctly. Each test backs up the real artifacts (if present) and
restores them on teardown so the script is hermetic.
"""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_dashboard.py"
DATA_JS = REPO_ROOT / "dashboard" / "assets" / "data.js"

# Real artifacts we might overwrite; backup/restore around the test.
PROTECTED = ["junit.xml", "flaky-tests.json", "coverage.xml", "mutmut-junit.xml"]


@pytest.fixture(autouse=True)
def hermetic_artifacts():
    """Backup + restore the 4 artifacts around each test."""
    backups: dict[str, bytes | None] = {}
    for name in PROTECTED:
        p = REPO_ROOT / name
        backups[name] = p.read_bytes() if p.is_file() else None
        if p.is_file():
            p.unlink()
    # Also backup the existing data.js so we can confirm the render
    # wrote a fresh one.
    data_backup = DATA_JS.read_bytes() if DATA_JS.is_file() else None
    try:
        yield
    finally:
        # Restore the 4 protected artifacts.
        for name, content in backups.items():
            p = REPO_ROOT / name
            if content is None:
                if p.is_file():
                    p.unlink()
            else:
                p.write_bytes(content)
        # Restore the original data.js.
        if data_backup is None:
            if DATA_JS.is_file():
                DATA_JS.unlink()
        else:
            DATA_JS.write_bytes(data_backup)


def _run_render(env: dict[str, str] | None = None) -> str:
    """Run render_dashboard.py and return the new data.js text."""
    proc = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT)],
        cwd=REPO_ROOT,
        env={**__import__("os").environ, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "wrote" in proc.stdout, f"unexpected output: {proc.stdout!r}"
    assert DATA_JS.is_file(), "render script must write dashboard/assets/data.js"
    return DATA_JS.read_text()


def _parse(text: str) -> dict:
    """Extract the JSON block from a window.DASHBOARD_DATA = ... ; line."""
    return json.loads(text.split("window.DASHBOARD_DATA = ", 1)[1].rstrip(";\n"))


def test_render_writes_valid_data_js_with_no_artifacts() -> None:
    """With no artifacts present, data.js must still be valid + safe defaults."""
    text = _run_render()
    parsed = _parse(text)
    assert parsed["totals"]["passed"] == 0
    assert parsed["totals"]["failed"] == 0
    assert parsed["totals"]["skipped"] == 0
    assert parsed["totals"]["duration"] == 0
    assert parsed["totals"]["passRate"] == 0
    assert parsed["mutation"]["skipped"] is True
    assert parsed["coverage"]["percent"] == 0


def test_render_parses_junit_totals() -> None:
    """junit.xml feeds totals + slowest list."""
    junit = ET.Element("testsuites")
    suite = ET.SubElement(
        junit,
        "testsuite",
        {
            "name": "pytest",
            "tests": "5",
            "failures": "1",
            "errors": "0",
            "skipped": "1",
            "time": "3.50",
        },
    )
    for i, (name, t) in enumerate(
        [
            ("test_a", "0.10"),
            ("test_b", "0.50"),
            ("test_c", "1.50"),  # slowest
            ("test_d", "0.25"),
            ("test_e_fail", "0.05"),
        ]
    ):
        tc = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"tests.unit.test_{i}",
                "name": name,
                "time": t,
            },
        )
        if name == "test_e_fail":
            ET.SubElement(tc, "failure", {"message": "assert 0"})
    (REPO_ROOT / "junit.xml").write_text(ET.tostring(junit, encoding="unicode"))

    parsed = _parse(_run_render())
    assert parsed["totals"]["passed"] == 3  # 5 - 1 - 0 - 1
    assert parsed["totals"]["failed"] == 1
    assert parsed["totals"]["skipped"] == 1
    assert parsed["totals"]["errors"] == 0
    assert parsed["totals"]["testCount"] == 5
    assert parsed["totals"]["duration"] == 3.5
    assert 0 < parsed["totals"]["passRate"] < 1
    assert parsed["slowest"][0]["name"].endswith("test_c")


def test_render_parses_flaky() -> None:
    """flaky-tests.json contributes the flaky + broken counts."""
    (REPO_ROOT / "flaky-tests.json").write_text(
        json.dumps(
            {
                "flaky": [
                    {"nodeid": "tests/x.py::test_y", "attempts": 2, "outcome": "passed"},
                    {"nodeid": "tests/x.py::test_z", "attempts": 3, "outcome": "passed"},
                ],
                "broken": [
                    {"nodeid": "tests/x.py::test_w", "attempts": 2, "outcome": "failed"},
                ],
            }
        )
    )
    parsed = _parse(_run_render())
    assert parsed["flaky"]["flakyCount"] == 2
    assert parsed["flaky"]["brokenCount"] == 1


def test_render_parses_coverage() -> None:
    """coverage.xml contributes the overall percent + per-file list."""
    cov = ET.Element(
        "coverage",
        {
            "line-rate": "0.8822",
            "lines-covered": "4049",
            "lines-valid": "4589",
            "branch-rate": "0.80",
        },
    )
    for path, rate in [
        ("src/seharness/a.py", "0.95"),
        ("src/seharness/b.py", "0.42"),
        ("src/seharness/c.py", "0.88"),
    ]:
        ET.SubElement(cov, "class", {"filename": path, "line-rate": rate})
    (REPO_ROOT / "coverage.xml").write_text(ET.tostring(cov, encoding="unicode"))

    parsed = _parse(_run_render())
    assert abs(parsed["coverage"]["percent"] - 0.8822) < 1e-6
    assert parsed["coverage"]["coveredLines"] == 4049
    assert parsed["coverage"]["totalLines"] == 4589
    paths = {f["path"] for f in parsed["coverage"]["perFile"]}
    assert "src/seharness/b.py" in paths


def test_render_parses_mutmut() -> None:
    """mutmut-junit.xml contributes killed/survived/timeout/total."""
    suite = ET.Element("testsuite", {"tests": "5"})
    # 3 killed (no failure/skipped), 1 survived (failure), 1 timeout (skipped)
    for i in range(3):
        ET.SubElement(suite, "testcase", {"name": f"killed_{i}"})
    tc_surv = ET.SubElement(suite, "testcase", {"name": "survived"})
    ET.SubElement(tc_surv, "failure", {"message": "survived"})
    tc_to = ET.SubElement(suite, "testcase", {"name": "timeout"})
    ET.SubElement(tc_to, "skipped")
    root = ET.Element("testsuites")
    root.append(suite)
    (REPO_ROOT / "mutmut-junit.xml").write_text(ET.tostring(root, encoding="unicode"))

    parsed = _parse(_run_render())
    assert parsed["mutation"]["killed"] == 3
    assert parsed["mutation"]["survived"] == 1
    assert parsed["mutation"]["timeout"] == 1
    assert parsed["mutation"]["total"] == 5
    assert abs(parsed["mutation"]["killRate"] - 0.6) < 1e-6
    assert parsed["mutation"]["skipped"] is False


def test_render_meta_env_vars() -> None:
    """DASHBOARD_* env vars propagate into the meta block."""
    parsed = _parse(
        _run_render(
            env={
                "DASHBOARD_GENERATED_AT": "2026-07-19T20:00:00Z",
                "DASHBOARD_COMMIT_SHA": "abc1234567890",
                "DASHBOARD_RUN_ID": "12345",
                "DASHBOARD_BRANCH": "main",
                "DASHBOARD_WORKFLOW_URL": "https://example/run/12345",
            }
        )
    )
    assert parsed["generatedAt"] == "2026-07-19T20:00:00Z"
    assert parsed["meta"]["commitSha"] == "abc1234567890"
    assert parsed["meta"]["runId"] == "12345"
    assert parsed["meta"]["branch"] == "main"
    assert parsed["meta"]["workflowUrl"] == "https://example/run/12345"


def test_render_does_not_crash_on_malformed_coverage() -> None:
    """Malformed coverage.xml yields zero coverage (not a crash)."""
    (REPO_ROOT / "coverage.xml").write_text("<not-valid-xml")
    parsed = _parse(_run_render())
    assert parsed["coverage"]["percent"] == 0
    assert parsed["coverage"]["coveredLines"] == 0
