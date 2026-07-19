"""RED tests for the G1c flaky-test detector.

Cluster G story G1c (Level 3: test analytics). The flaky detector
ingests per-test outcomes from the pytest run and produces a
``FlakyReport`` with:

  * ``flaky_tests`` — passed after at least one rerun
  * ``broken_tests`` — failed and exhausted retries
  * ``rerun_counts`` — nodeid → int (how many times pytest reran it)

The detector is a pure function over an in-memory events list so it
can be unit-tested without spawning pytest. The plugin side (which
*produces* the events from pytest's hook) lives in
``tests/_testing_helpers/flaky_plugin.py`` and is exercised by an
end-to-end test in ``test_flaky_plugin_smoke.py``.

Refs: docs/analysis/2026-07-19-priority-stories.md G1c.
"""

from __future__ import annotations

from tests._testing_helpers.flaky_detector import (
    RerunEvent,
    analyze_events,
)


def test_no_events_yields_empty_report() -> None:
    """No rerun events → empty FlakyReport."""
    report = analyze_events([])
    assert report.flaky_tests == []
    assert report.broken_tests == []
    assert report.rerun_counts == {}


def test_single_pass_no_reruns() -> None:
    """A test that passed on the first try is neither flaky nor broken."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="passed"),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == []
    assert report.broken_tests == []
    assert report.rerun_counts == {}


def test_pass_after_rerun_is_flaky() -> None:
    """A test that failed once, then passed, is flaky."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="passed"),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == ["tests/test_x.py::test_a"]
    assert report.broken_tests == []
    assert report.rerun_counts == {"tests/test_x.py::test_a": 1}


def test_pass_after_multiple_reruns_is_flaky() -> None:
    """A test that failed twice, then passed, is flaky (counted as 2 reruns)."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="passed"),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == ["tests/test_x.py::test_a"]
    assert report.rerun_counts == {"tests/test_x.py::test_a": 2}


def test_failed_after_exhausting_retries_is_broken() -> None:
    """A test that failed on every attempt is broken, not flaky."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_a", outcome="failed"),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == []
    assert report.broken_tests == ["tests/test_x.py::test_a"]
    assert report.rerun_counts == {"tests/test_x.py::test_a": 2}


def test_mixed_pass_flaky_broken() -> None:
    """A run with one pass, one flaky, one broken is reported correctly."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_pass", outcome="passed"),
        RerunEvent(nodeid="tests/test_x.py::test_flaky", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_flaky", outcome="passed"),
        RerunEvent(nodeid="tests/test_x.py::test_broken", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_broken", outcome="failed"),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == ["tests/test_x.py::test_flaky"]
    assert report.broken_tests == ["tests/test_x.py::test_broken"]
    assert report.rerun_counts == {
        "tests/test_x.py::test_flaky": 1,
        "tests/test_x.py::test_broken": 1,
    }


def test_report_serializes_to_json() -> None:
    """FlakyReport must round-trip through JSON for the GH Actions artifact."""
    events = [
        RerunEvent(nodeid="tests/test_x.py::test_flaky", outcome="failed"),
        RerunEvent(nodeid="tests/test_x.py::test_flaky", outcome="passed"),
    ]
    report = analyze_events(events)
    payload = report.to_dict()
    assert payload == {
        "flaky_tests": ["tests/test_x.py::test_flaky"],
        "broken_tests": [],
        "rerun_counts": {"tests/test_x.py::test_flaky": 1},
        "summary": {
            "total_flaky": 1,
            "total_broken": 0,
            "total_reruns": 1,
        },
    }


def test_nodeid_with_spaces_preserved() -> None:
    """nodeids with spaces (parametrize ids) are preserved verbatim."""
    events = [
        RerunEvent(
            nodeid="tests/test_x.py::test_a[some-id with spaces]",
            outcome="failed",
        ),
        RerunEvent(
            nodeid="tests/test_x.py::test_a[some-id with spaces]",
            outcome="passed",
        ),
    ]
    report = analyze_events(events)
    assert report.flaky_tests == ["tests/test_x.py::test_a[some-id with spaces]"]
