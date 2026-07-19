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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DATA = REPO_ROOT / "dashboard" / "assets" / "data.js"
HISTORY_JSONL = REPO_ROOT / "dashboard" / "assets" / "history.jsonl"

# G12b: trend window size. Show the last N rows in sparkline charts.
TREND_WINDOW = 30


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


# ----------------------------------------------------------------------
# G12b — trendline history
# ----------------------------------------------------------------------


def parse_history(path: Path) -> list[dict[str, Any]]:
    """Read history.jsonl and return a list of metric rows.

    Defensive: a missing file returns []. Malformed lines are skipped
    (the dashboard should never crash on a typo in the history file).
    """
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        print(f"  warn: could not read {path}: {exc}", file=sys.stderr)
        return []
    return rows


def compute_trends(
    history: list[dict[str, Any]], window: int = TREND_WINDOW
) -> dict[str, list[float]]:
    """Reduce history rows into per-metric trend arrays.

    Returns a dict mapping metric name → list of floats (last N rows,
    oldest first). Each row in `history` must have numeric values for
    `tests`, `passRate`, `coverage`, `mutation`. Rows missing a metric
    are skipped for that metric only.
    """
    tail = history[-window:] if window > 0 else history
    out: dict[str, list[float]] = {
        "tests": [],
        "passRate": [],
        "coverage": [],
        "mutation": [],
    }
    for row in tail:
        for key, default in out.items():
            value = row.get(key, default)
            if isinstance(value, (int, float)):
                default.append(float(value))
    return out


def append_history(path: Path, row: dict[str, Any]) -> int:
    """Append a single row to history.jsonl. Returns the new total row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return sum(1 for _ in path.open("r", encoding="utf-8")) if path.is_file() else 1


def build_history_row(
    junit: dict[str, Any],
    coverage: dict[str, Any],
    mutation: dict[str, Any],
    meta: dict[str, str],
) -> dict[str, Any]:
    """Construct one history row from the current render's parsed metrics."""
    return {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commitSha": meta.get("commitSha", ""),
        "runId": meta.get("runId", ""),
        "tests": junit.get("testCount", 0) or 0,
        "passRate": junit.get("passRate", 0.0) or 0.0,
        "coverage": coverage.get("percent", 0.0) or 0.0,
        "mutation": mutation.get("killRate", 0.0) or 0.0,
    }


def build_data_js(
    junit: dict[str, Any],
    flaky: dict[str, Any],
    coverage: dict[str, Any],
    mutation: dict[str, Any],
    trends: dict[str, list[float]],
) -> str:
    """Render the data.js content with the snapshot + trendlines."""
    snapshot: dict[str, Any] = {
        "generatedAt": os.environ.get("DASHBOARD_GENERATED_AT")
        or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
        "trends": trends,
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
    # Allow tests to redirect inputs via env (otherwise the defaults point
    # into the repo workspace, which is what CI and local both want).
    if "DASHBOARD_DATA_OUT" in os.environ:
        out_path = Path(os.environ["DASHBOARD_DATA_OUT"])
    else:
        out_path = DASHBOARD_DATA
    if "HISTORY_JSONL" in os.environ:
        history_path = Path(os.environ["HISTORY_JSONL"])
    else:
        history_path = HISTORY_JSONL

    junit = parse_junit_totals(junit_path)
    flaky = parse_flaky(flaky_path)
    coverage = parse_coverage(coverage_path)
    mutation = parse_mutmut_junit(mutmut_path)

    # G12b: read existing history, compute trends, append current row.
    history = parse_history(history_path)
    trends = compute_trends(history)
    row = build_history_row(
        junit,
        coverage,
        mutation,
        {
            "commitSha": os.environ.get("DASHBOARD_COMMIT_SHA", ""),
            "runId": os.environ.get("DASHBOARD_RUN_ID", ""),
        },
    )
    append_history(history_path, row)
    # Refresh trends to include the row we just appended.
    history = parse_history(history_path)
    trends = compute_trends(history)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_data_js(junit, flaky, coverage, mutation, trends))
    print(
        f"  wrote {out_path} ({out_path.stat().st_size} bytes); "
        f"history rows={len(history)}; trends keys={sorted(trends)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
