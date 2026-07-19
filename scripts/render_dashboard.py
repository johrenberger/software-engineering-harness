#!/usr/bin/env python3
"""Render the engineering dashboard data sidecar from CI artifacts.

Reads:
    junit.xml          (pytest totals + slowest tests)
    flaky-tests.json   (G1c flaky + broken counts)
    coverage.xml       (coverage.py XML output)
    mutmut-junit.xml   (mutmut JUnit XML output; optional)

Writes:
    dashboard/assets/data.js

The script is idempotent and defensive: any missing file produces
zero-valued fields, never a crash, so a partial CI failure still
results in a deployable page.

Designed to run as the ``render-dashboard`` step in ci.yml after the
gates; invoked once per CI push to main.
"""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DATA = REPO_ROOT / "dashboard" / "assets" / "data.js"


def _safe_load(path: Path, *, loader: Any) -> Any:
    """Load a file or return None; never raise."""
    if not path.is_file():
        return None
    try:
        return loader(path)
    except Exception as exc:
        print(f"  warn: could not parse {path}: {exc}", file=sys.stderr)
        return None


def parse_junit_totals(path: Path) -> dict[str, Any]:
    """Return totals + slowest-10 from a JUnit XML report."""
    tree = _safe_load(path, loader=ET.parse)
    if tree is None:
        return {
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
            "duration": 0.0,
            "passRate": 0.0,
            "testCount": 0,
            "slowest": [],
        }
    root = tree.getroot()
    suites = root.findall(".//testsuite") if root.tag != "testsuite" else [root]
    if not suites and root.tag == "testsuites":
        suites = root.findall("testsuite")

    passed = failed = skipped = errors = 0
    duration = 0.0
    slowest: list[tuple[float, str]] = []
    for suite in suites:
        passed += (
            int(suite.get("tests", "0") or 0)
            - int(suite.get("failures", "0") or 0)
            - int(suite.get("errors", "0") or 0)
            - int(suite.get("skipped", "0") or 0)
        )
        failed += int(suite.get("failures", "0") or 0)
        errors += int(suite.get("errors", "0") or 0)
        skipped += int(suite.get("skipped", "0") or 0)
        duration += float(suite.get("time", "0") or 0.0)
        for tc in suite.findall("testcase"):
            t = float(tc.get("time", "0") or 0.0)
            name = tc.get("name", "")
            cls = tc.get("classname", "")
            full = f"{cls}::{name}" if cls else name
            slowest.append((t, full))

    # cap passed to >= 0 (some junit variants count negative when
    # assertions are subtractive).
    passed = max(passed, 0)
    test_count = passed + failed + errors + skipped
    pass_rate = (passed / test_count) if test_count > 0 else 0.0
    slowest.sort(reverse=True)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors,
        "duration": round(duration, 2),
        "passRate": round(pass_rate, 4),
        "testCount": test_count,
        "slowest": [{"name": name, "duration": round(d, 3)} for d, name in slowest[:10]],
    }


def parse_flaky(path: Path) -> dict[str, Any]:
    """Return the flaky + broken counts + a few examples."""
    data = _safe_load(path, loader=lambda p: json.loads(p.read_text()))
    if not isinstance(data, dict):
        return {"flakyCount": 0, "brokenCount": 0, "examples": []}
    flaky_list = data.get("flaky", []) or []
    broken_list = data.get("broken", []) or []
    examples = []
    for entry in list(flaky_list) + list(broken_list):
        if isinstance(entry, dict):
            examples.append(
                {
                    "nodeid": entry.get("nodeid", entry.get("id", "?")),
                    "attempts": entry.get("attempts", 0),
                    "finalOutcome": entry.get("final_outcome", entry.get("outcome", "?")),
                }
            )
        examples = examples[:5]
    return {
        "flakyCount": len(flaky_list),
        "brokenCount": len(broken_list),
        "examples": examples,
    }


def parse_coverage(path: Path) -> dict[str, Any]:
    """Return overall coverage percent + line counts."""
    tree = _safe_load(path, loader=ET.parse)
    if tree is None:
        return {"percent": 0.0, "coveredLines": 0, "totalLines": 0, "perFile": []}
    root = tree.getroot()
    # coverage.py XML: root is <coverage>, line-rate is the percent as float.
    line_rate = root.get("line-rate")
    lines_covered = root.get("lines-covered")
    lines_valid = root.get("lines-valid")
    per_file: list[dict[str, Any]] = []
    for cls in root.findall(".//class"):
        rate = cls.get("line-rate")
        if rate is None:
            continue
        try:
            pct = float(rate)
        except ValueError:
            continue
        per_file.append(
            {
                "path": cls.get("filename", ""),
                "percent": pct,
            }
        )
    per_file.sort(key=lambda x: x["percent"])  # lowest first
    return {
        "percent": float(line_rate) if line_rate is not None else 0.0,
        "coveredLines": int(lines_covered) if lines_covered else 0,
        "totalLines": int(lines_valid) if lines_valid else 0,
        "perFile": per_file[-50:],  # cap to 50 lowest
    }


def parse_mutmut_junit(path: Path) -> dict[str, Any]:
    """Return killed / survived / timeout / total from mutmut's JUnit XML.

    mutmut 2.5.1's junitxml output is a standard <testsuite> with one
    <testcase> per mutant. The case name carries the mutant id; status
    is encoded via <failure> (survived) or <skipped> (timeout). We count:
        killed  = no <failure> AND no <skipped>
        survived = has <failure>
        timeout  = has <skipped>
    """
    tree = _safe_load(path, loader=ET.parse)
    if tree is None:
        # If mutmut didn't run (no src/ changes), mark skipped.
        return {
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "total": 0,
            "killRate": 0.0,
            "skipped": True,
        }
    root = tree.getroot()
    cases = root.findall(".//testcase") if root.tag != "testcase" else [root]
    killed = survived = timeout = 0
    for tc in cases:
        if tc.find("failure") is not None:
            survived += 1
        elif tc.find("skipped") is not None or tc.find("error") is not None:
            timeout += 1
        else:
            killed += 1
    total = killed + survived + timeout
    kill_rate = (killed / total) if total > 0 else 0.0
    return {
        "killed": killed,
        "survived": survived,
        "timeout": timeout,
        "total": total,
        "killRate": round(kill_rate, 4),
        "skipped": False,
    }


def build_data_js(
    junit: dict[str, Any], flaky: dict[str, Any], coverage: dict[str, Any], mutation: dict[str, Any]
) -> str:
    """Render the data.js content with the snapshot."""
    snapshot: dict[str, Any] = {
        "generatedAt": os.environ.get("DASHBOARD_GENERATED_AT", ""),
        "totals": {
            "passed": junit["passed"],
            "failed": junit["failed"],
            "skipped": junit["skipped"],
            "errors": junit["errors"],
            "duration": junit["duration"],
            "passRate": junit["passRate"],
            "testCount": junit["testCount"],
        },
        "coverage": coverage,
        "slowest": junit["slowest"],
        "flaky": flaky,
        "mutation": mutation,
        # buildHistory is populated client-side via the GH Actions API.
        "buildHistory": [],
        "meta": {
            "branch": os.environ.get("DASHBOARD_BRANCH", "main"),
            "commitSha": os.environ.get("DASHBOARD_COMMIT_SHA", ""),
            "runId": os.environ.get("DASHBOARD_RUN_ID", ""),
            "workflowUrl": os.environ.get("DASHBOARD_WORKFLOW_URL", ""),
        },
    }
    # Use json.dumps with reasonable formatting for diff-friendly output.
    body = json.dumps(snapshot, indent=2, sort_keys=True)
    return (
        "// Auto-generated by scripts/render_dashboard.py — do not edit by hand.\n"
        "// Snapshot from the latest CI push to main.\n\n"
        f"window.DASHBOARD_DATA = {body};\n"
    )


def main() -> int:
    junit_path = REPO_ROOT / "junit.xml"
    flaky_path = REPO_ROOT / "flaky-tests.json"
    coverage_path = REPO_ROOT / "coverage.xml"
    mutmut_path = REPO_ROOT / "mutmut-junit.xml"

    junit = parse_junit_totals(junit_path)
    flaky = parse_flaky(flaky_path)
    coverage = parse_coverage(coverage_path)
    mutation = parse_mutmut_junit(mutmut_path)

    DASHBOARD_DATA.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_DATA.write_text(build_data_js(junit, flaky, coverage, mutation))
    print(
        f"  wrote {DASHBOARD_DATA.relative_to(REPO_ROOT)} ({DASHBOARD_DATA.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
