"""Workflow-shape contract tests for Cluster G story G2 Slice 2 (enforcement).

Slice 1 (PR #21) shipped mutmut in report-only mode. Slice 2 flips
the gate: when a PR's mutmut run has more than ``max_survivors``
survivors, the CI job must fail.

Approach:
  * A small ``scripts/mutation_gate.py`` parses ``mutmut-junit.xml``
    (the JUnit XML already produced by mutmut's ``junitxml`` subcommand)
    and exits non-zero if survivors > threshold. The PR-only
    ``mutation-test`` step in ci.yml chains mutmut → mutation_gate.
  * ``continue-on-error: true`` is REMOVED from the ``mutation-test``
    step on Slice 2.
  * Configuration lives in ``pyproject.toml`` under ``[tool.mutation_gate]``
    with ``max_survivors = 5`` (matches current baseline observed on
    ``main @ 0423f95``: 41 killed / 5 survived out of 110 mutants).
  * The script is defensive: a missing/malformed junit-xml is treated
    as 0 survivors (do not fail on infrastructure glitches).
"""

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_SCRIPT = REPO_ROOT / "scripts" / "mutation_gate.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"


# ----------------------------------------------------------------------
# 1. scripts/mutation_gate.py exists + parses junit XML
# ----------------------------------------------------------------------


def test_mutation_gate_script_exists() -> None:
    """The gate script must be checked in at scripts/mutation_gate.py."""
    assert GATE_SCRIPT.is_file(), (
        "scripts/mutation_gate.py must exist (Slice 2 wrapper around "
        "mutmut-junit.xml that enforces max_survivors)"
    )


def test_mutation_gate_script_is_executable() -> None:
    """The gate script must have a working Python shebang."""
    text = GATE_SCRIPT.read_text()
    assert text.startswith("#!/usr/bin/env python"), (
        "scripts/mutation_gate.py must start with the Python shebang"
    )


# ----------------------------------------------------------------------
# 2. Gate behaviour: parses junit XML + threshold logic
# ----------------------------------------------------------------------


def _write_synthetic_junit(path: Path, killed: int, survived: int, timeout: int) -> None:
    """Write a synthetic mutmut-junit.xml to `path`."""
    suite = ET.Element("testsuite", {"tests": str(killed + survived + timeout)})
    for i in range(killed):
        ET.SubElement(suite, "testcase", {"name": f"killed_{i}"})
    for i in range(survived):
        tc = ET.SubElement(suite, "testcase", {"name": f"survived_{i}"})
        ET.SubElement(tc, "failure", {"message": "survived"})
    for i in range(timeout):
        tc = ET.SubElement(suite, "testcase", {"name": f"timeout_{i}"})
        ET.SubElement(tc, "skipped")
    root = ET.Element("testsuites")
    root.append(suite)
    path.write_text(ET.tostring(root, encoding="unicode"))


def _run_gate(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run scripts/mutation_gate.py with optional env overrides."""
    import os

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_mutation_gate_passes_when_survivors_below_threshold(tmp_path: Path) -> None:
    """0 survivors → exit code 0 (gate passes)."""
    junit = tmp_path / "mutmut-junit.xml"
    _write_synthetic_junit(junit, killed=10, survived=0, timeout=0)
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "5"})
    assert proc.returncode == 0, (
        f"gate must pass when survivors=0 (got exit {proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "killed=10" in proc.stdout or "killed: 10" in proc.stdout, (
        f"gate must report killed count in stdout; got: {proc.stdout!r}"
    )


def test_mutation_gate_passes_when_survivors_at_threshold(tmp_path: Path) -> None:
    """5 survivors with max_survivors=5 → exit code 0 (gate passes at boundary)."""
    junit = tmp_path / "mutmut-junit.xml"
    _write_synthetic_junit(junit, killed=10, survived=5, timeout=0)
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "5"})
    assert proc.returncode == 0, (
        f"gate must pass at threshold boundary (got exit {proc.returncode}):\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_mutation_gate_fails_when_survivors_exceed_threshold(tmp_path: Path) -> None:
    """6 survivors with max_survivors=5 → exit code 1 (gate fails)."""
    junit = tmp_path / "mutmut-junit.xml"
    _write_synthetic_junit(junit, killed=10, survived=6, timeout=0)
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "5"})
    assert proc.returncode == 1, (
        f"gate must FAIL when survivors (6) > threshold (5); got exit {proc.returncode}"
    )
    assert "6" in proc.stdout and "5" in proc.stdout, (
        f"gate failure output must include survivor count + threshold; got: {proc.stdout!r}"
    )


def test_mutation_gate_handles_missing_junit_as_zero_survivors(tmp_path: Path) -> None:
    """Missing mutmut-junit.xml → exit 0 (gate passes; don't fail CI on infra issues).

    Mirrors the design choice in the dashboard render script: any
    missing file produces safe defaults, never a crash.
    """
    junit = tmp_path / "does-not-exist.xml"
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "5"})
    assert proc.returncode == 0, (
        f"missing junit must NOT fail the gate (got exit {proc.returncode}):\nstderr={proc.stderr}"
    )


def test_mutation_gate_handles_malformed_junit_as_zero_survivors(tmp_path: Path) -> None:
    """Malformed mutmut-junit.xml → exit 0 (defensive)."""
    junit = tmp_path / "mutmut-junit.xml"
    junit.write_text("<not-valid-xml")
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "5"})
    assert proc.returncode == 0, (
        f"malformed junit must NOT fail the gate (got exit {proc.returncode}):\n"
        f"stderr={proc.stderr}"
    )


def test_mutation_gate_counts_survived_via_failure_element(tmp_path: Path) -> None:
    """Survivors are counted from <testcase><failure> children.

    This matches mutmut 2.5.1's junitxml output convention: killed
    mutants have no <failure> child; survived mutants have one.
    """
    junit = tmp_path / "mutmut-junit.xml"
    # Write a JUnit XML with 2 failed (survived) and 3 passed (killed).
    suite = ET.Element("testsuite", {"tests": "5"})
    for i in range(3):
        ET.SubElement(suite, "testcase", {"name": f"killed_{i}"})
    for i in range(2):
        tc = ET.SubElement(suite, "testcase", {"name": f"survived_{i}"})
        ET.SubElement(tc, "failure", {"message": "survived"})
    root = ET.Element("testsuites")
    root.append(suite)
    junit.write_text(ET.tostring(root, encoding="unicode"))

    # With threshold = 1, the 2 survivors must fail the gate.
    proc = _run_gate(env={"MUTMUT_JUNIT_XML": str(junit), "MAX_SURVIVORS": "1"})
    assert proc.returncode == 1, (
        f"gate must fail with 2 survivors vs threshold 1; got exit {proc.returncode}"
    )


# ----------------------------------------------------------------------
# 3. CI workflow integration: gate is invoked + continue-on-error removed
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow_text() -> str:
    return CI_WORKFLOW.read_text()


def test_ci_workflow_chains_gate_after_mutmut(ci_workflow_text: str) -> None:
    """The mutation-test step must invoke the gate after mutmut run.

    The simplest wiring: the same step that runs mutmut then calls
    ``scripts/mutation_gate.py`` and propagates its exit code.
    """
    assert "mutation_gate.py" in ci_workflow_text, (
        "ci.yml must invoke scripts/mutation_gate.py after mutmut run"
    )
    # Look for the actual INVOCATIONS (lines starting with `python`
    # or `mutmut`), not the comments that mention these tools.
    mutmut_invocation_re = re.compile(r"^\s*mutmut junitxml\s*>", re.MULTILINE)
    gate_invocation_re = re.compile(r"^\s*python scripts/mutation_gate\.py", re.MULTILINE)
    mutmut_matches = list(mutmut_invocation_re.finditer(ci_workflow_text))
    gate_matches = list(gate_invocation_re.finditer(ci_workflow_text))
    assert len(mutmut_matches) >= 1, "ci.yml must invoke `mutmut junitxml > ...` at least once"
    assert len(gate_matches) == 1, (
        "ci.yml must invoke `python scripts/mutation_gate.py` exactly once"
    )
    assert gate_matches[0].start() > mutmut_matches[0].start(), (
        "mutation_gate.py must be invoked AFTER mutmut junitxml (it consumes the file mutmut wrote)"
    )


def test_ci_workflow_continues_on_error_removed(ci_workflow_text: str) -> None:
    """The mutation-test step must NOT have continue-on-error: true on Slice 2.

    Slice 1 had continue-on-error: true for report-only mode. Slice 2
    flips to enforcement: a non-zero exit must fail the CI job.
    """
    # Find the mutation-test step block.
    mut_idx = ci_workflow_text.find("- name: mutation-test")
    assert mut_idx > 0, "mutation-test step must exist in ci.yml"
    # Find the next step that follows (heuristic: next "\n      - " or
    # "\n      - name:" or end of file).
    block_end = ci_workflow_text.find("\n      - ", mut_idx + 1)
    block_end = block_end if block_end > 0 else len(ci_workflow_text)
    block = ci_workflow_text[mut_idx:block_end]
    # The block must NOT contain `continue-on-error: true` directly under
    # the mutation-test step (it's fine if it's under a sub-step like
    # the mutmut-only command itself; but Slice 2 standard: the GATE
    # must NOT be marked continue-on-error).
    assert "continue-on-error: true" not in block, (
        "Slice 2: mutation-test step must NOT have continue-on-error: true "
        "(the gate must be enforced; failure must fail the CI job)"
    )


def test_ci_workflow_passes_max_survivors_env(ci_workflow_text: str) -> None:
    """The gate must be invoked with MAX_SURVIVORS env var set.

    The threshold value comes from ``[tool.mutation_gate].max_survivors``
    in pyproject.toml (or a hardcoded sensible default).
    """
    # Look for the env: block on the gate invocation.
    assert "MAX_SURVIVORS:" in ci_workflow_text, (
        "ci.yml must pass MAX_SURVIVORS env var to scripts/mutation_gate.py"
    )


def test_ci_workflow_uploads_junit_when_gate_fails(ci_workflow_text: str) -> None:
    """The mutation artifacts upload must still run on gate failure.

    So a failed gate still leaves mutmut-junit.xml available for the
    engineer to inspect (which mutants survived? where in the code?).
    """
    # upload-mutation-artifacts step must use `if: always()` or similar.
    upload_idx = ci_workflow_text.find("- name: upload-mutation-artifacts")
    assert upload_idx > 0, "upload-mutation-artifacts step must exist"
    block_end = ci_workflow_text.find("\n      - ", upload_idx + 1)
    block_end = block_end if block_end > 0 else len(ci_workflow_text)
    block = ci_workflow_text[upload_idx:block_end]
    assert "if: always()" in block or "if: success() || failure()" in block, (
        "upload-mutation-artifacts must run on gate failure too "
        "(engineers need the junit file to debug survivors)"
    )


# ----------------------------------------------------------------------
# 4. pyproject.toml configuration
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return PYPROJECT.read_text()


def test_pyproject_declares_mutation_gate_config(pyproject_text: str) -> None:
    """pyproject.toml must have a [tool.mutation_gate] block."""
    assert "[tool.mutation_gate]" in pyproject_text, (
        "pyproject.toml must declare [tool.mutation_gate] with the threshold"
    )


def test_pyproject_max_survivors_is_set(pyproject_text: str) -> None:
    """The threshold must be declared as max_survivors = <int>."""
    m = re.search(r"max_survivors\s*=\s*(\d+)", pyproject_text)
    assert m is not None, (
        "[tool.mutation_gate] must declare max_survivors = <int> (baseline-observed value: 5)"
    )
    assert int(m.group(1)) > 0, f"max_survivors must be > 0; got {m.group(1)}"
