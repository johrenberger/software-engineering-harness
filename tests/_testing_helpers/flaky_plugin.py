"""pytest plugin: capture flaky-test events for G1c.

This plugin listens to ``pytest_runtest_logreport`` (called once per
test outcome, including each rerun) and accumulates :class:`RerunEvent`
records per nodeid. After the run, it writes a JSON artifact for
downstream consumers (GH Actions job summary, engineering dashboard).

The plugin is intentionally tiny: it doesn't re-implement flaky
detection (that's :func:`analyze_events`), it just records raw
events. The pure analyzer is unit-tested in
``test_flaky_detector.py``; the plugin's IO + lifecycle is
end-to-end tested in ``test_flaky_plugin_smoke.py``.

Configuration:

  * ``--seharness-flaky-output=<path>`` — write the JSON report to
    this path. If unset, the plugin still records events in memory
    but doesn't write anything.

Refs: docs/analysis/2026-07-19-priority-stories.md G1c.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._testing_helpers.flaky_detector import (
    RerunEvent,
    analyze_events,
)

_OUTCOMES_OF_INTEREST = frozenset({"passed", "failed", "rerun"})


class _FlakyRecorder:
    """Per-session recorder. One per pytest run."""

    def __init__(self) -> None:
        self._events: list[RerunEvent] = []

    def record(self, nodeid: str, outcome: str, *, phase: str) -> None:
        if outcome not in _OUTCOMES_OF_INTEREST:
            return
        if phase != "call":
            # Only the call phase represents the actual test outcome.
            # setup/teardown failures are recorded separately by pytest
            # (not relevant for flaky detection).
            return
        self._events.append(RerunEvent(nodeid=nodeid, outcome=outcome))  # type: ignore[arg-type]

    def report(self) -> tuple[list[RerunEvent], dict[str, object]]:
        events = list(self._events)
        flaky = analyze_events(events)
        return events, flaky.to_dict()

    def write(self, path: Path) -> None:
        import json

        _events, payload = self.report()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


# pytest config doesn't have a stable "stash" in pytest_runtest_logreport
# (TestReport has no .config). Use a module-level dict keyed by the
# pytest Session's id() — pytest creates exactly one Session per run.
_RECORDERS: dict[int, tuple[_FlakyRecorder, Path | None]] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--seharness-flaky-output",
        action="store",
        default=None,
        help="Path to write the flaky-test JSON report (cluster G G1c).",
    )


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    """Register the recorder for this session."""
    output = config.getoption("--seharness-flaky-output")
    out_path: Path | None = Path(output) if output else None
    # The Session is created later; cache config so pytest_sessionstart can
    # wire it up.
    config._seharness_flaky_output = out_path  # type: ignore[attr-defined]


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    """Bind a recorder to this Session."""
    out_path: Path | None = getattr(session.config, "_seharness_flaky_output", None)
    recorder = _FlakyRecorder()
    _RECORDERS[id(session)] = (recorder, out_path)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Capture per-attempt call-phase outcomes."""
    session = getattr(report, "session", None)
    if session is not None:
        entry = _RECORDERS.get(id(session))
    elif len(_RECORDERS) == 1:
        # pytest <7 fallback: there's exactly one recorder.
        entry = next(iter(_RECORDERS.values()))
    else:
        entry = None
    if entry is None:
        return
    recorder, _output = entry
    recorder.record(
        nodeid=report.nodeid,
        outcome=report.outcome,
        phase=report.when,
    )


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int,  # noqa: ARG001
) -> None:
    entry = _RECORDERS.pop(id(session), None)
    if entry is None:
        return
    recorder, output = entry
    if output is None:
        return
    recorder.write(output)
