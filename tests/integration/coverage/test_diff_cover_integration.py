"""Integration tests for G1b's diff-cover gate.

These tests run ``pytest --cov`` + ``diff-cover`` end-to-end against
a temporary Python file added to ``src/seharness/``. They verify:

  * A new file with full test coverage passes the gate.
  * A new file with uncovered lines fails the gate (exit code 1).

The tests use ``subprocess`` to invoke ``diff-cover`` so they exercise
the actual CLI the CI workflow calls. They are slow (~10 s) so
they're marked ``integration`` per the existing test taxonomy.

Refs: docs/analysis/2026-07-19-priority-stories.md G1b.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COVERAGE_XML = REPO_ROOT / "coverage.xml"
FLOOR_PCT = 80


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, returning CompletedProcess with text output."""
    return subprocess.run(  # noqa: S603 — test-only subprocess
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
        **kwargs,
    )


def _write_module(name: str, body: str) -> Path:
    """Create src/seharness/<name>/__init__.py with the given body. Returns module path."""
    module_dir = REPO_ROOT / "src" / "seharness" / name
    module_dir.mkdir(exist_ok=True)
    init = module_dir / "__init__.py"
    init.write_text(body)
    return init


def _write_test(name: str, body: str) -> Path:
    """Create tests/integration/<name>/test_basic.py with the given body."""
    test_dir = REPO_ROOT / "tests" / "integration" / name
    test_dir.mkdir(parents=True, exist_ok=True)
    test = test_dir / "test_basic.py"
    test.write_text(body)
    return test


def _cleanup(name: str) -> None:
    """Remove the fixture directories."""
    for d in (
        REPO_ROOT / "src" / "seharness" / name,
        REPO_ROOT / "tests" / "integration" / name,
    ):
        shutil.rmtree(d, ignore_errors=True)
    # Drop stale coverage artifacts.
    for p in REPO_ROOT.glob(".coverage*"):
        p.unlink(missing_ok=True)
    COVERAGE_XML.unlink(missing_ok=True)
    # Unstage anything left behind.
    _run(["git", "reset", "HEAD", "--", f"src/seharness/{name}", f"tests/integration/{name}"])


FULLY_COVERED_MODULE = '''"""G1b fixture: fully covered."""

from __future__ import annotations


def ok() -> str:
    return "ok"


def classify(x: int) -> str:
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    return "zero"
'''

FULLY_COVERED_TEST = '''"""G1b fixture: covers everything."""

from __future__ import annotations

from seharness._g1b_test_covered import classify, ok


def test_ok() -> None:
    assert ok() == "ok"


def test_classify_positive() -> None:
    assert classify(5) == "positive"


def test_classify_negative() -> None:
    assert classify(-5) == "negative"


def test_classify_zero() -> None:
    assert classify(0) == "zero"
'''

UNDERCOVERED_MODULE = '''"""G1b fixture: intentionally under-tested."""

from __future__ import annotations


def covered_one() -> str:
    return "one"


def uncovered_a(x: int) -> str:
    if x > 100:
        return "huge"
    elif x > 10:
        return "big"
    elif x > 0:
        return "small"
    return "zero"


def uncovered_b() -> int:
    for _ in range(50):
        pass
    return 1
'''

UNDERCOVERED_TEST = '''"""G1b fixture: only covers one branch."""

from __future__ import annotations

from seharness._g1b_test_uncovered import covered_one


def test_covered_one() -> None:
    assert covered_one() == "one"
'''


@pytest.mark.integration
def test_diff_cover_passes_on_fully_covered_new_module() -> None:
    """A new src file with full coverage passes the 80% diff-cover gate."""
    name = "_g1b_test_covered"
    try:
        _write_module(name, FULLY_COVERED_MODULE)
        _write_test(name, FULLY_COVERED_TEST)
        # Stage the new module so diff-cover sees it in HEAD's diff vs its
        # parent. Use the explicit path (git add -A doesn't work with
        # mid-path globs in some git versions).
        _run(["git", "add", "src/seharness/" + name + "/__init__.py"])

        # Run pytest to produce coverage.xml
        pytest_run = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/" + name,
                "-o",
                "addopts=-ra --strict-markers --strict-config --cov= --cov-branch --cov-report=xml:"
                + str(COVERAGE_XML),
                "--cov=seharness." + name,
                "--cov-branch",
                "--cov-report=xml:" + str(COVERAGE_XML),
                "--cov-fail-under=0",
                "-q",
                "--no-header",
            ]
        )
        assert pytest_run.returncode == 0, (
            f"pytest failed:\nstdout={pytest_run.stdout}\nstderr={pytest_run.stderr}"
        )
        assert COVERAGE_XML.exists(), "coverage.xml not produced"

        # Run diff-cover via the diff_cover.diff_cover_tool module. The
        # ``--compare-branch=HEAD`` compares the staged changes (the new
        # file) against HEAD, which is exactly the diff-cover CLI that CI
        # calls for a PR (where ``origin/main`` is HEAD~1 of the PR branch).
        result = _run(
            [
                sys.executable,
                "-m",
                "diff_cover.diff_cover_tool",
                str(COVERAGE_XML),
                "--compare-branch=HEAD",
                "--src-roots=src",
                f"--fail-under={FLOOR_PCT}",
            ]
        )
        assert result.returncode == 0, (
            f"diff-cover should pass with full coverage, got exit "
            f"{result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        # The fixture is fully covered, so coverage should be 100%.
        assert "100%" in result.stdout or "Coverage: 1.0" in result.stdout
    finally:
        _cleanup(name)


@pytest.mark.integration
def test_diff_cover_fails_on_undercovered_new_module() -> None:
    """A new src file with uncovered lines fails the 80% diff-cover gate."""
    name = "_g1b_test_uncovered"
    try:
        _write_module(name, UNDERCOVERED_MODULE)
        _write_test(name, UNDERCOVERED_TEST)
        _run(["git", "add", "src/seharness/" + name + "/__init__.py"])

        pytest_run = _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/" + name,
                "-o",
                "addopts=-ra --strict-markers --strict-config --cov= --cov-branch --cov-report=xml:"
                + str(COVERAGE_XML),
                "--cov=seharness." + name,
                "--cov-branch",
                "--cov-report=xml:" + str(COVERAGE_XML),
                "--cov-fail-under=0",
                "-q",
                "--no-header",
            ]
        )
        assert pytest_run.returncode == 0, (
            f"pytest failed:\nstdout={pytest_run.stdout}\nstderr={pytest_run.stderr}"
        )
        assert COVERAGE_XML.exists(), "coverage.xml not produced"

        result = _run(
            [
                sys.executable,
                "-m",
                "diff_cover.diff_cover_tool",
                str(COVERAGE_XML),
                "--compare-branch=HEAD",
                "--src-roots=src",
                f"--fail-under={FLOOR_PCT}",
            ]
        )
        assert result.returncode != 0, (
            f"diff-cover should fail with under-coverage, got exit 0\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        combined = result.stdout + "\n" + result.stderr
        assert "Failure" in combined or "below" in combined.lower()
    finally:
        _cleanup(name)


@pytest.mark.integration
def test_diff_cover_module_invokable() -> None:
    """Sanity check: diff-cover's module can be invoked via Python."""
    result = _run([sys.executable, "-m", "diff_cover.diff_cover_tool", "--version"])
    assert result.returncode == 0
    assert "diff-cover" in result.stdout.lower()
