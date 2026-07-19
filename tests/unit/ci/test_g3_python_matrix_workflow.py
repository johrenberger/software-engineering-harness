"""Contract tests for G3 — Python-version CI matrix.

G3 makes the CI workflow exercise every supported Python version, not
just one. This catches Python-version-specific regressions at PR time
instead of downstream. The matrix must match pyproject.toml's
`requires-python` and the `Programming Language :: Python :: 3.XX`
classifiers.

References:
- G3 spec: docs/analysis/2026-07-19-priority-stories.md
- pyproject.toml: requires-python + classifiers
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text())


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_WORKFLOW.read_text()


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return PYPROJECT.read_text()


@pytest.fixture(scope="module")
def pyproject_data() -> dict:
    # Use tomllib (3.11+) — falls back to manual parsing otherwise.
    import sys

    if sys.version_info >= (3, 11):
        import tomllib

        with PYPROJECT.open("rb") as fh:
            return tomllib.load(fh)
    pytest.skip("requires Python 3.11+ tomllib")


def _matrix_python_versions(ci: dict) -> list[str]:
    return (
        ci.get("jobs", {})
        .get("quality-gate", {})
        .get("strategy", {})
        .get("matrix", {})
        .get("python-version", [])
    )


def _classifiers_for_python(pyproject: dict) -> set[str]:
    out: set[str] = set()
    for c in pyproject.get("project", {}).get("classifiers", []):
        m = re.match(r"Programming Language :: Python :: (\d+\.\d+)", c)
        if m:
            out.add(m.group(1))
    return out


def _requires_python_floor(pyproject: dict) -> str:
    """Return the minimum Python version from `requires-python` (e.g. '3.12')."""
    rp = pyproject.get("project", {}).get("requires-python", "")
    m = re.search(r">=\s*(\d+\.\d+)", rp)
    return m.group(1) if m else ""


# ----------------------------------------------------------------------
# 1. Matrix exists and has multiple versions
# ----------------------------------------------------------------------


def test_ci_workflow_defines_python_matrix(ci_workflow: dict) -> None:
    """ci.yml must define a Python-version matrix (G3)."""
    matrix = (
        ci_workflow.get("jobs", {}).get("quality-gate", {}).get("strategy", {}).get("matrix", {})
    )
    assert "python-version" in matrix, "ci.yml must declare a `matrix.python-version` entry (G3)."
    versions = matrix["python-version"]
    assert isinstance(versions, list) and len(versions) >= 2, (
        f"G3 requires at least 2 Python versions in the matrix; got {versions!r}. "
        f"A single-version matrix defeats the purpose of G3."
    )


def test_python_matrix_includes_3_12_and_3_13(ci_workflow: dict) -> None:
    """Matrix must include Python 3.12 and 3.13 (matches pyproject classifiers)."""
    versions = _matrix_python_versions(ci_workflow)
    assert "3.12" in versions, (
        f"Python 3.12 must be in the CI matrix (pyproject requires-python = '>=3.12'). "
        f"Got: {versions}"
    )
    assert "3.13" in versions, (
        f"Python 3.13 must be in the CI matrix (current default, pyproject classifier). "
        f"Got: {versions}"
    )


def test_fail_fast_is_false(ci_workflow: dict) -> None:
    """fail-fast: false so we get full signal in one PR cycle."""
    strategy = ci_workflow.get("jobs", {}).get("quality-gate", {}).get("strategy", {})
    assert strategy.get("fail-fast") is False, (
        "G3 requires `fail-fast: false` so both Python versions run even if "
        "one fails; otherwise we lose the second matrix signal in one PR cycle."
    )


# ----------------------------------------------------------------------
# 2. Matrix matches pyproject.toml's supported set
# ----------------------------------------------------------------------


def test_matrix_aligns_with_pyproject_classifiers(ci_workflow: dict, pyproject_data: dict) -> None:
    """Every matrix version must appear in pyproject classifiers.

    Otherwise we're testing a Python version we don't claim to support.
    """
    matrix = set(_matrix_python_versions(ci_workflow))
    classifiers = _classifiers_for_python(pyproject_data)
    unsupported = matrix - classifiers
    assert not unsupported, (
        f"CI matrix versions {unsupported} are not declared in "
        f"pyproject.toml's classifiers. Add the classifier first "
        f"(so users know we claim to support them), then test them."
    )


def test_matrix_covers_requires_python_floor(ci_workflow: dict, pyproject_data: dict) -> None:
    """The matrix MUST include the minimum supported Python version.

    If requires-python = '>=3.12', we MUST test on 3.12 (not just 3.13).
    """
    floor = _requires_python_floor(pyproject_data)
    assert floor, "pyproject.toml has no `requires-python = >=X.Y` directive"
    matrix = _matrix_python_versions(ci_workflow)
    assert floor in matrix, (
        f"CI matrix must include Python {floor} (the requires-python floor). Got: {matrix}"
    )


# ----------------------------------------------------------------------
# 3. No regressions: existing CI behavior preserved
# ----------------------------------------------------------------------


def test_ci_workflow_still_runs_on_pull_request(ci_workflow: dict) -> None:
    """G3 must not change the PR trigger."""
    on = ci_workflow.get(True) or ci_workflow.get("on", {})
    pr = on.get("pull_request", {})
    assert "main" in pr.get("branches", []), "ci.yml must still trigger on pull_request to main."


def test_setup_python_step_uses_matrix(ci_workflow: dict) -> None:
    """Set up Python step must reference the matrix variable."""
    steps = ci_workflow.get("jobs", {}).get("quality-gate", {}).get("steps", [])
    setup = next((s for s in steps if s.get("name", "").startswith("Set up Python")), None)
    assert setup is not None, "ci.yml must include a 'Set up Python' step"
    py_version = setup.get("with", {}).get("python-version", "")
    assert "matrix.python-version" in py_version, (
        f"Set up Python step must reference ${{{{ matrix.python-version }}}}; "
        f"got `python-version: {py_version!r}`"
    )


# ----------------------------------------------------------------------
# 4. Workflow has not accidentally re-introduced `runs-on` per-matrix
# ----------------------------------------------------------------------


def test_runs_on_is_not_per_matrix(ci_workflow: dict) -> None:
    """runs-on must be at job level (not inside matrix)."""
    job = ci_workflow["jobs"]["quality-gate"]
    runs_on = job.get("runs-on", "")
    assert runs_on == "ubuntu-latest", (
        f"runs-on must be `ubuntu-latest` at job level (not inside matrix). Got: {runs_on!r}"
    )


# ----------------------------------------------------------------------
# 5. Comment in YAML matches the G3 rationale
# ----------------------------------------------------------------------


def test_ci_text_documents_g3(ci_text: str) -> None:
    """The ci.yml should have a comment block explaining G3."""
    assert "G3" in ci_text, (
        "ci.yml should include a `# G3:` comment explaining the matrix rationale"
    )
    # Should mention both versions somewhere.
    assert "3.12" in ci_text and "3.13" in ci_text, (
        "ci.yml should document the supported Python versions in comments"
    )
