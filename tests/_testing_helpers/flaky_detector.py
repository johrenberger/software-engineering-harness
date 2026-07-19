"""Pure-function flaky-test analyzer.

Cluster G story G1c (Level 3 test analytics). This module is
intentionally side-effect free: it ingests a list of
:class:`RerunEvent` records and returns a :class:`FlakyReport`.
The pytest plugin that *produces* RerunEvent records lives in
``flaky_plugin.py``; this module is unit-tested in isolation.

Terminology:

  * **flaky** — passed after one or more failures (reran successfully)
  * **broken** — failed on every attempt (exhausted retries)
  * **clean pass** — passed on the first attempt

A test that was *configured* with ``--reruns=N`` and passed on
attempt ``k`` where ``1 < k <= N+1`` is flaky (reran ``k-1`` times).
A test that failed on attempt ``N+1`` is broken (exhausted retries).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

Outcome = Literal["passed", "failed", "rerun", "skipped"]


@dataclass(frozen=True)
class RerunEvent:
    """One outcome recorded for a single test attempt."""

    nodeid: str
    outcome: Outcome


@dataclass
class FlakyReport:
    """Aggregated analysis of a test run's rerun outcomes."""

    flaky_tests: list[str] = field(default_factory=list)
    broken_tests: list[str] = field(default_factory=list)
    rerun_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def total_flaky(self) -> int:
        return len(self.flaky_tests)

    @property
    def total_broken(self) -> int:
        return len(self.broken_tests)

    @property
    def total_reruns(self) -> int:
        return sum(self.rerun_counts.values())

    def to_dict(self) -> dict[str, object]:
        """Serialize for JSON artifact (consumed by GH Actions + dashboard)."""
        return {
            "flaky_tests": sorted(self.flaky_tests),
            "broken_tests": sorted(self.broken_tests),
            "rerun_counts": dict(sorted(self.rerun_counts.items())),
            "summary": {
                "total_flaky": self.total_flaky,
                "total_broken": self.total_broken,
                "total_reruns": self.total_reruns,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def analyze_events(events: list[RerunEvent]) -> FlakyReport:
    """Compute the FlakyReport from a flat list of per-attempt outcomes.

    The events list is grouped by nodeid (preserving order). For each
    nodeid, the events are scanned in order:

      * Last outcome is ``passed`` → the test is **flaky** if any earlier
        outcome was ``failed``, else a clean pass.
      * Last outcome is ``failed`` → the test is **broken** (all
        retries exhausted).
      * Otherwise (skipped) → ignored for the flaky/broken tally.

    The ``rerun_counts`` dict maps nodeid → number of *reruns*
    observed. A rerun is any attempt after the first; that is
    ``reruns = total_attempts - 1``. For a flaky test that took 3
    attempts to pass (1 failure, 2 passes), ``reruns = 2``. For a
    broken test that failed 3 times (3 failures, 0 passes),
    ``reruns = 2``. Clean passes (``reruns = 0``) are not recorded.
    """
    report = FlakyReport()
    by_node: dict[str, list[Outcome]] = defaultdict(list)
    for ev in events:
        by_node[ev.nodeid].append(ev.outcome)

    for nodeid, outcomes in by_node.items():
        attempts = len(outcomes)
        # "rerun" (from pytest-rerunfailures) is the same as "failed" for
        # the purposes of flaky/broken classification — it indicates an
        # attempt that did not pass.
        failures = sum(1 for o in outcomes if o in ("failed", "rerun"))
        reruns = attempts - 1  # any attempt beyond the first
        final = outcomes[-1]
        if final == "passed" and failures > 0:
            report.flaky_tests.append(nodeid)
            report.rerun_counts[nodeid] = reruns
        elif final in ("failed", "rerun") and failures > 0:
            # If the final outcome is itself a rerun, the test is broken
            # (exhausted retries).
            report.broken_tests.append(nodeid)
            report.rerun_counts[nodeid] = reruns

    return report
