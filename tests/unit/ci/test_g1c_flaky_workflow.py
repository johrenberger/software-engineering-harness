"""Workflow-shape contract tests for Cluster G story G1c.

Cluster G adds two CI steps for test analytics:

  * ``flaky-test-report`` — appends a markdown summary derived from
    ``flaky-tests.json`` to ``$GITHUB_STEP_SUMMARY``.
  * ``upload-test-artifacts`` — uploads ``junit.xml`` + ``flaky-tests.json``
    to the run's artifacts (consumed by the G12 dashboard).

This test mirrors the structural assertions used in
``test_g1b_diff_cover_workflow.py`` and confirms both steps are wired
correctly in ``.github/workflows/ci.yml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/ci.yml")


def _read() -> dict[str, object]:
    text = WORKFLOW.read_text()
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict), "ci.yml did not parse as a YAML mapping"
    return parsed


def test_workflow_file_exists() -> None:
    assert WORKFLOW.exists(), f"missing workflow file: {WORKFLOW}"


def test_workflow_parses_as_yaml() -> None:
    cfg = _read()
    assert cfg.get("name") == "ci"


def test_flaky_report_step_present() -> None:
    text = WORKFLOW.read_text()
    assert "- name: flaky-test-report" in text, "missing G1c step: flaky-test-report"


def test_flaky_report_writes_to_github_step_summary() -> None:
    text = WORKFLOW.read_text()
    # Locate the body of the flaky-test-report step.
    m = re.search(
        r"- name: flaky-test-report\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m, "could not find flaky-test-report step body"
    body = m.group(0)
    assert "GITHUB_STEP_SUMMARY" in body, "flaky-test-report must write to $GITHUB_STEP_SUMMARY"


def test_flaky_report_runs_on_always() -> None:
    """The report must surface even on pytest failures."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: flaky-test-report\s*\n\s+if: always\(\).+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m, "flaky-test-report must use 'if: always()'"


def test_flaky_report_reads_flaky_tests_json() -> None:
    text = WORKFLOW.read_text()
    assert "flaky-tests.json" in text, "flaky-test-report must read flaky-tests.json"


def test_upload_test_artifacts_step_present() -> None:
    text = WORKFLOW.read_text()
    assert "- name: upload-test-artifacts" in text, "missing G1c step: upload-test-artifacts"


def test_upload_test_artifacts_uploads_junit_xml() -> None:
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: upload-test-artifacts\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m, "could not find upload-test-artifacts step body"
    body = m.group(0)
    assert "junit.xml" in body, "upload-test-artifacts must include junit.xml"
    assert "flaky-tests.json" in body, "upload-test-artifacts must include flaky-tests.json"


def test_upload_test_artifacts_uses_actions_upload_artifact() -> None:
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: upload-test-artifacts\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m
    body = m.group(0)
    assert "actions/upload-artifact@" in body, (
        "upload-test-artifacts must use actions/upload-artifact@<sha> # v4"
    )


def test_upload_test_artifacts_runs_on_always() -> None:
    """Artifacts must be uploaded even on pytest failure for postmortem."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: upload-test-artifacts\s*\n\s+if: always\(\).+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m, "upload-test-artifacts must use 'if: always()'"


def test_pytest_step_remains_before_flaky_step() -> None:
    """G1c steps depend on pytest having produced junit.xml + flaky-tests.json."""
    text = WORKFLOW.read_text()
    pytest_idx = text.find("- name: pytest\n")
    flaky_idx = text.find("- name: flaky-test-report")
    assert pytest_idx > 0 and flaky_idx > pytest_idx, "flaky-test-report must run AFTER pytest"


def test_addopts_includes_flaky_output() -> None:
    """pyproject.toml must keep --seharness-flaky-output so CI tests produce JSON."""
    pyproject = Path("pyproject.toml")
    text = pyproject.read_text()
    assert "--seharness-flaky-output=" in text, (
        "pyproject.toml addopts must include --seharness-flaky-output="
    )


def test_addopts_includes_reruns() -> None:
    """pyproject.toml must keep --reruns=N for retry-based flaky detection."""
    pyproject = Path("pyproject.toml")
    text = pyproject.read_text()
    assert "--reruns=" in text, "pyproject.toml addopts must include --reruns="


def test_addopts_includes_junit_xml() -> None:
    """pyproject.toml must keep --junit-xml=junit.xml for the artifact uploader."""
    pyproject = Path("pyproject.toml")
    text = pyproject.read_text()
    assert "--junit-xml=junit.xml" in text, (
        "pyproject.toml addopts must include --junit-xml=junit.xml"
    )


def test_dev_extra_includes_pytest_rerunfailures() -> None:
    """pyproject.toml dev extras must include pytest-rerunfailures for the reruns CLI flag."""
    pyproject = Path("pyproject.toml")
    text = pyproject.read_text()
    assert "pytest-rerunfailures" in text, (
        "pyproject.toml dev extras must include pytest-rerunfailures"
    )


def test_workflow_preserves_g1b_steps() -> None:
    """Pre-existing G1b steps must remain after G1c addition."""
    text = WORKFLOW.read_text()
    for step in (
        "- name: coverage-diff-check",
        "- name: coverage-summary",
        "- name: coverage-diff-summary",
    ):
        assert step in text, f"G1b step missing: {step}"


def test_workflow_preserves_quality_gates() -> None:
    """Pre-existing quality gates (ruff, mypy, bandit, pip-audit) must remain."""
    text = WORKFLOW.read_text()
    for step in (
        "ruff format check",
        "ruff check",
        "mypy strict",
        "bandit",
        "pip-audit",
        "pytest",
    ):
        assert step in text, f"pre-existing quality gate missing: {step}"
