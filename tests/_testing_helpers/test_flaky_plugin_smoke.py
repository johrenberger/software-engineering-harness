"""End-to-end test for the G1c flaky-test plugin.

Verifies that invoking pytest with the plugin loaded produces the
expected ``flaky-tests.json`` artifact. Uses a throwaway fixture
test that is flaky on its first two runs but passes on the third.

Refs: docs/analysis/2026-07-19-priority-stories.md G1c.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAKY_JSON = REPO_ROOT / "flaky-tests.json"
JUNIT_XML = REPO_ROOT / "junit.xml"

# The flaky plugin is registered via ``tests/conftest.py``. Pytest only
# auto-loads conftest.py from the rootdir upward, so the inner subprocess
# must run with the **repo root** as cwd (or with --rootdir + conftest
# explicitly). We use PYTHONPATH so the plugin module is importable too.
PLUGIN_MODULE = "tests._testing_helpers.flaky_plugin"


@pytest.fixture(autouse=True)
def _cleanup_artifacts() -> object:
    """Wipe flaky-tests.json + junit.xml before and after each test."""
    for f in (FLAKY_JSON, JUNIT_XML):
        f.unlink(missing_ok=True)
    yield None
    for f in (FLAKY_JSON, JUNIT_XML):
        f.unlink(missing_ok=True)


def _base_argv() -> list[str]:
    """Build a -o addopts override that disables coverage for the inner subprocess.

    The inner subprocess pytest invocation must NOT inherit the outer pytest's
    addopts (otherwise it re-enables coverage, JUnit XML, and (most importantly)
    ``--reruns=N`` which would cascade). An empty ``--cov=`` argument clears
    coverage paths.
    """
    return ["-o", "addopts=-ra --strict-markers --strict-config --cov="]  # empty: coverage off


def _env() -> dict[str, str]:
    """Build an env dict that lets the inner subprocess load our plugin module."""
    env = {**os.environ}
    py_path = env.get("PYTHONPATH", "")
    src = str(REPO_ROOT / "src")
    tests_root = str(REPO_ROOT)
    new_pp = ":".join(p for p in (tests_root, src, py_path) if p)
    env["PYTHONPATH"] = new_pp
    return env


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — test-only subprocess
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        env=_env(),
    )


def _write_flaky_fixture(fixture_dir: Path) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "__init__.py").write_text("")
    test_file = fixture_dir / "test_flaky.py"
    test_file.write_text(
        '"""Throwaway plugin-smoke fixture (cleaned up after itself)."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        '_attempt = {"n": 0}\n'
        "\n"
        "\n"
        "def test_flaky() -> None:\n"
        '    _attempt["n"] += 1\n'
        '    assert _attempt["n"] >= 3\n'
        "\n"
        "\n"
        "def test_pass() -> None:\n"
        "    assert 1 + 1 == 2\n"
    )


def _write_passing_fixture(fixture_dir: Path, name: str) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "__init__.py").write_text("")
    (fixture_dir / f"test_{name}.py").write_text("def test_passes() -> None:\n    assert True\n")


def _write_always_failing_fixture(fixture_dir: Path) -> None:
    fixture_dir.mkdir(parents=True, exist_ok=True)
    (fixture_dir / "__init__.py").write_text("")
    (fixture_dir / "test_broken.py").write_text(
        "def test_always_fails() -> None:\n    assert False, 'boom'\n"
    )


def test_plugin_emits_flaky_json_for_flaky_test(tmp_path: Path) -> None:
    """A deliberately flaky test produces a flaky-tests.json with the right shape."""
    fixture_dir = tmp_path / "_g1c_plugin_smoke"
    _write_flaky_fixture(fixture_dir)
    output = tmp_path / "flaky.json"
    output.unlink(missing_ok=True)

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(fixture_dir),
            "--reruns=3",
            f"--seharness-flaky-output={output}",
            "-p",
            f"{PLUGIN_MODULE}",
            "-q",
            "--no-cov",
            "--no-header",
            *_base_argv(),
        ]
    )
    assert result.returncode == 0, (
        f"pytest failed (rc={result.returncode}):\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert output.exists(), (
        f"flaky-tests.json not produced. pytest stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    payload = json.loads(output.read_text())
    assert payload["summary"]["total_flaky"] == 1
    # pytest reports nodeid relative to the test session rootdir; we only
    # verify the test name matches because the absolute path is environmental.
    assert len(payload["flaky_tests"]) == 1
    assert payload["flaky_tests"][0].endswith("test_flaky.py::test_flaky")
    assert payload["broken_tests"] == []
    assert any(k.endswith("test_flaky.py::test_flaky") for k in payload["rerun_counts"])
    # The 2 reruns must be recorded.
    flaky_count = next(
        v for k, v in payload["rerun_counts"].items() if k.endswith("test_flaky.py::test_flaky")
    )
    assert flaky_count == 2, (
        f"expected 2 reruns, got {flaky_count}; full rerun_counts={payload['rerun_counts']}"
    )
    # The always-pass test must not be in any list.
    assert not any(
        nodeid.endswith("test_flaky.py::test_pass")
        for nodeid in payload["flaky_tests"] + payload["broken_tests"]
    )


def test_plugin_writes_empty_json_when_no_reruns(tmp_path: Path) -> None:
    """When --reruns=0, the JSON is still produced (empty)."""
    fixture_dir = tmp_path / "_g1c_no_reruns"
    _write_passing_fixture(fixture_dir, "passes")
    output = tmp_path / "no-reruns.json"
    output.unlink(missing_ok=True)

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(fixture_dir),
            f"--seharness-flaky-output={output}",
            "--reruns=0",
            "-p",
            f"{PLUGIN_MODULE}",
            "-q",
            "--no-cov",
            "--no-header",
            *_base_argv(),
        ]
    )
    assert result.returncode == 0, f"pytest failed:\n{result.stderr}"
    assert output.exists()
    payload = json.loads(output.read_text())
    assert payload["summary"]["total_flaky"] == 0
    assert payload["summary"]["total_reruns"] == 0


def test_plugin_records_call_phase_only(tmp_path: Path) -> None:
    """The plugin sees call-phase outcomes; setup/teardown are not counted."""
    fixture_dir = tmp_path / "_g1c_phase_filter"
    _write_passing_fixture(fixture_dir, "clean")
    output = tmp_path / "phase-filter.json"
    output.unlink(missing_ok=True)

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(fixture_dir),
            f"--seharness-flaky-output={output}",
            "--reruns=0",
            "-p",
            f"{PLUGIN_MODULE}",
            "-q",
            "--no-cov",
            "--no-header",
            *_base_argv(),
        ]
    )
    assert result.returncode == 0, f"pytest failed:\n{result.stderr}"
    payload = json.loads(output.read_text())
    assert payload["summary"]["total_flaky"] == 0
    assert payload["summary"]["total_reruns"] == 0
    assert payload["flaky_tests"] == []


def test_plugin_handles_broken_test(tmp_path: Path) -> None:
    """A test that fails all attempts is classified as broken (not flaky)."""
    fixture_dir = tmp_path / "_g1c_broken"
    _write_always_failing_fixture(fixture_dir)
    output = tmp_path / "broken.json"
    output.unlink(missing_ok=True)

    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(fixture_dir),
            "--reruns=2",
            f"--seharness-flaky-output={output}",
            "-p",
            f"{PLUGIN_MODULE}",
            "-q",
            "--no-cov",
            "--no-header",
            *_base_argv(),
        ]
    )
    # pytest returns nonzero because the test is broken.
    assert result.returncode != 0, (
        f"pytest should fail when a test always fails.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert output.exists(), (
        f"plugin must write JSON even on broken tests.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    payload = json.loads(output.read_text())
    assert payload["summary"]["total_broken"] == 1
    assert payload["summary"]["total_flaky"] == 0
    assert len(payload["broken_tests"]) == 1
    assert payload["broken_tests"][0].endswith("test_broken.py::test_always_fails")
    broken_count = next(
        v
        for k, v in payload["rerun_counts"].items()
        if k.endswith("test_broken.py::test_always_fails")
    )
    assert broken_count == 2, (
        f"expected 2 reruns on broken test, got {broken_count}; full={payload['rerun_counts']}"
    )
