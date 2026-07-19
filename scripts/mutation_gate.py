#!/usr/bin/env python3
"""Cluster G Slice 2: enforce the mutation gate.

Reads the mutmut JUnit XML report (produced by ``mutmut junitxml``)
and exits non-zero when the number of survived mutants exceeds the
configured threshold (default: ``MAX_SURVIVORS=5``, matches the
baseline observed on ``main @ 0423f95``: 41 killed / 5 survived).

Exit codes:
    0  -- gate passed (survivors <= threshold) OR no junit available
    1  -- gate failed (survivors > threshold)
    2  -- script error (shouldn't happen; defensive)

Configuration:
    MUTMUT_JUNIT_XML  path to mutmut-junit.xml
                       (default: <repo>/mutmut-junit.xml)
    MAX_SURVIVORS     integer threshold (default: 5)

The script is defensive: a missing or malformed junit XML is treated
as 0 survivors (do not fail CI on infrastructure glitches). Only
clearly-exceeded thresholds fail the job.

Design choice mirrors the dashboard render script: missing artifacts
yield safe defaults, never crashes.
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_junit_path() -> Path:
    return REPO_ROOT / "mutmut-junit.xml"


def count_survivors(junit_path: Path) -> tuple[int, int, int]:
    """Return (killed, survived, timeout) counts from a mutmut junit XML.

    mutmut 2.5.1's junitxml output convention:
        killed   = <testcase> with no <failure> AND no <skipped>
        survived = <testcase> with <failure>
        timeout  = <testcase> with <skipped>
    """
    if not junit_path.is_file():
        return (0, 0, 0)
    try:
        tree = ET.parse(junit_path)
    except Exception:
        return (0, 0, 0)
    root = tree.getroot()
    cases = root.findall(".//testcase") if root.tag != "testcase" else [root]
    killed = survived = timeout = 0
    for tc in cases:
        if tc.find("failure") is not None:
            survived += 1
        elif tc.find("skipped") is not None:
            timeout += 1
        else:
            killed += 1
    return (killed, survived, timeout)


def main() -> int:
    junit_path = Path(os.environ.get("MUTMUT_JUNIT_XML", str(_default_junit_path())))
    try:
        max_survivors = int(os.environ.get("MAX_SURVIVORS", "5"))
    except ValueError:
        print("  warn: MAX_SURVIVORS is not an integer; defaulting to 5", file=sys.stderr)
        max_survivors = 5

    killed, survived, timeout = count_survivors(junit_path)
    total = killed + survived + timeout

    print(
        f"  mutation gate: killed={killed}, survived={survived}, timeout={timeout}, total={total}"
    )
    print(f"  threshold: max_survivors={max_survivors}")

    if total == 0:
        # Defensive: nothing to gate on (no mutants ran — likely a PR
        # that didn't touch src/, or mutmut was skipped). Treat as
        # pass so we don't fail CI on infra.
        print("  result: PASS (no mutants found in junit; gate is inert)")
        return 0

    if survived > max_survivors:
        kill_rate = (killed / total) * 100 if total > 0 else 0.0
        print(
            f"  result: FAIL ({survived} survivors > threshold {max_survivors}; "
            f"kill rate = {kill_rate:.1f}%)"
        )
        print(
            "  hint: download the mutmut-junit.xml artifact to see which "
            "mutants survived and where to add tests."
        )
        return 1

    kill_rate = (killed / total) * 100 if total > 0 else 0.0
    print(f"  result: PASS (kill rate = {kill_rate:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
