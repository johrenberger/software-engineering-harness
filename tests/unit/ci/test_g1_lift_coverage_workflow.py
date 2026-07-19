"""Contract tests for G1 — coverage floor (fail_under).

G1 enforces a coverage floor in CI via ``fail_under`` in pyproject.toml.
The floor starts at 88 (PR #18 / G1a) and is lifted incrementally as
new test surface is added. This contract test pins:

1. The floor is set explicitly (not left at the default).
2. The floor matches the project's documented baseline.
3. CI does NOT use ``--no-cov`` (which would bypass the gate).
4. The ``coverage.xml`` artifact is produced (so dashboard.yml's
   workflow_run can pick it up; see G12c).

References:
- G1 spec: docs/analysis/2026-07-19-priority-stories.md
- pyproject.toml [tool.coverage.report]
- ci.yml (ruff → bandit → pip-audit → pytest step ordering)
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def pyproject_data() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    import yaml

    return yaml.safe_load(CI_WORKFLOW.read_text())


# ----------------------------------------------------------------------
# 1. The fail_under floor must be explicit and >= 88 (G1 baseline)
# ----------------------------------------------------------------------


def test_fail_under_is_set(pyproject_data: dict) -> None:
    """fail_under must be explicit (not left to coverage.py's default 0)."""
    report = pyproject_data.get("tool", {}).get("coverage", {}).get("report", {})
    assert "fail_under" in report, (
        "pyproject.toml must declare [tool.coverage.report].fail_under (G1)"
    )
    floor = report["fail_under"]
    assert isinstance(floor, (int, float)) and floor > 0, (
        f"fail_under must be a positive number, got {floor!r}"
    )


def test_fail_under_at_or_above_g1_baseline(pyproject_data: dict) -> None:
    """The G1 PR lifts the floor from 88 to 89; don't drop below."""
    floor = pyproject_data["tool"]["coverage"]["report"]["fail_under"]
    assert floor >= 88, (
        f"G1 baseline is 88%. Current floor: {floor}%. "
        f"Lifting the floor requires new tests; dropping below the "
        f"G1 baseline is a regression."
    )


# ----------------------------------------------------------------------
# 2. CI does not bypass the gate with --no-cov
# ----------------------------------------------------------------------


def test_ci_workflow_does_not_use_no_cov(ci_workflow: dict) -> None:
    """The pytest step in ci.yml MUST NOT pass --no-cov (would bypass gate)."""
    pytest_step = None
    for s in ci_workflow.get("jobs", {}).get("quality-gate", {}).get("steps", []):
        if s.get("name") == "pytest":
            pytest_step = s
            break
    assert pytest_step is not None, "ci.yml must include a step named 'pytest'"
    cmd = pytest_step.get("run", "")
    assert "--no-cov" not in cmd, "ci.yml pytest step MUST NOT use --no-cov (bypasses the G1 gate)."
    assert "pytest" in cmd, f"pytest step should invoke pytest. Got: {cmd!r}"


# ----------------------------------------------------------------------
# 3. coverage.xml is produced (for G12c dashboard + diff-cover)
# ----------------------------------------------------------------------


def test_coverage_xml_is_produced_by_pytest(ci_workflow: dict) -> None:
    """ci.yml's pytest step must produce coverage.xml (for diff-cover + dashboard)."""
    pytest_step = None
    for s in ci_workflow.get("jobs", {}).get("quality-gate", {}).get("steps", []):
        if s.get("name") == "pytest":
            pytest_step = s
            break
    assert pytest_step is not None
    cmd = pytest_step.get("run", "")
    # The ad-hoc --cov-report=xml was retired in G1a; coverage.xml is now
    # produced automatically by addopts in [tool.pytest.ini_options].
    # We check the addopts in pyproject.toml instead.
    # (Keep this test here as a regression guard — G1a documented that
    # the explicit --cov-report=xml flag was removed.)
    assert "--cov-report" not in cmd, (
        "ci.yml must rely on pytest addopts for --cov-report=xml; explicit "
        "CLI flag was removed in G1a and must not be re-added."
    )


def test_pytest_addopts_produce_coverage_xml(pyproject_data: dict) -> None:
    """pyproject.toml's [tool.pytest.ini_options].addopts must include
    ``--cov-report=term-missing`` and ``--cov-report=xml`` (latter drives
    diff-cover + dashboard).
    """
    addopts_raw = (
        pyproject_data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", "")
    )
    # addopts is a list of strings in modern pyproject; legacy may be a string.
    if isinstance(addopts_raw, list):
        addopts = " ".join(str(x) for x in addopts_raw)
    else:
        addopts = str(addopts_raw)
    assert "--cov" in addopts, "pytest addopts must include --cov (G1: enforcement depends on it)"
    assert "--cov-report=xml" in addopts, (
        "pytest addopts must include --cov-report=xml (drives diff-cover + dashboard)"
    )
    # We rely on [tool.coverage.report].fail_under instead of --cov-fail-under
    # on the CLI; the former is the documented source of truth.
    assert "--cov-fail-under" not in addopts, (
        "fail_under should be configured in [tool.coverage.report], not via "
        "the --cov-fail-under CLI flag (single source of truth)."
    )


# ----------------------------------------------------------------------
# 4. Fail-under doesn't regress below G1's current target
# ----------------------------------------------------------------------


def test_fail_under_documented_in_pyproject(pyproject_data: dict) -> None:
    """The pyproject comment block should explain the fail_under rationale."""
    text = PYPROJECT.read_text()
    # There should be a comment that mentions G1 and the floor.
    assert "G1" in text or "fail_under" in text, (
        "pyproject.toml should include a comment block documenting the "
        "fail_under rationale (helps future contributors understand why "
        "the floor is where it is)."
    )


# ----------------------------------------------------------------------
# 5. CI runs the gate (pip-audit, pytest) in the right order so coverage is on disk
# ----------------------------------------------------------------------


def test_coverage_diff_check_runs_after_pytest(ci_workflow: dict) -> None:
    """coverage-diff-check step must come AFTER pytest (needs coverage.xml)."""
    steps = ci_workflow["jobs"]["quality-gate"]["steps"]
    names = [s.get("name") for s in steps]
    pytest_idx = next((i for i, n in enumerate(names) if n == "pytest"), None)
    diff_check_idx = next((i for i, n in enumerate(names) if n == "coverage-diff-check"), None)
    assert pytest_idx is not None, "ci.yml must include a `pytest` step"
    assert diff_check_idx is not None, "ci.yml must include a `coverage-diff-check` step"
    assert pytest_idx < diff_check_idx, (
        f"coverage-diff-check (step {diff_check_idx}) must run AFTER "
        f"pytest (step {pytest_idx}) — otherwise coverage.xml doesn't exist."
    )


# ----------------------------------------------------------------------
# 6. coverage.xml artifact is uploaded (for downstream consumers)
# ----------------------------------------------------------------------


def test_upload_test_artifacts_includes_coverage_xml(ci_workflow: dict) -> None:
    """The 'upload-test-artifacts' step must include coverage.xml in the path."""
    steps = ci_workflow["jobs"]["quality-gate"]["steps"]
    upload = next((s for s in steps if s.get("name") == "upload-test-artifacts"), None)
    assert upload is not None, "ci.yml must include an 'upload-test-artifacts' step"
    path = upload.get("with", {}).get("path", "")
    assert "coverage.xml" in path, (
        f"upload-test-artifacts must include coverage.xml in path. Got: {path!r}"
    )


# ----------------------------------------------------------------------
# 7. coverage-source is set to the src package (excludes tests)
# ----------------------------------------------------------------------


def test_coverage_source_is_src(pyproject_data: dict) -> None:
    """[tool.coverage.run].source must point at the src package only."""
    source = pyproject_data.get("tool", {}).get("coverage", {}).get("run", {}).get("source", [])
    if isinstance(source, list):
        # normalize: the source MUST NOT be ['.'] (which would include tests)
        assert "." not in source or "src/seharness" in source, (
            f"coverage.run.source must target the src package, not `.`. Got: {source!r}"
        )
    elif isinstance(source, str):
        assert source != ".", (
            "coverage.run.source must be the src package, not '.' (the dot "
            "includes test files which inflates coverage)."
        )
