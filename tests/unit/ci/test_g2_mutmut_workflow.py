"""Workflow-shape contract tests for Cluster G story G2 (Slice 1).

Cluster G Slice G2 (L4 mutation-gate - report-only first slice) wires
``mutmut`` into CI:

  * ``mutation-test`` step (PR-only) — runs ``mutmut run
    --use-patch-file /tmp/pr.patch`` against the PR diff vs origin/main.
    Reports kill rate but is ``continue-on-error: true`` on the first
    slice so we observe the baseline before enforcing. (mutmut 2.5.1
    forbids combining ``--use-coverage`` with ``--use-patch-file``.)
  * ``upload-mutation-artifacts`` step (always) — uploads the JUnit XML
    mutation report for downstream consumers (G12 dashboard).

This test mirrors the structural assertions used in
``test_g1b_diff_cover_workflow.py`` and ``test_g1c_flaky_workflow.py``
and confirms the new step is wired correctly in
``.github/workflows/ci.yml`` and that ``pyproject.toml`` has the
required ``[tool.mutmut]`` block.

Refs: ``docs/analysis/2026-07-19-priority-stories.md`` G2.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/ci.yml")
PYPROJECT = Path("pyproject.toml")


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), f"{WORKFLOW} missing"


def test_workflow_parses_as_yaml() -> None:
    parsed = yaml.safe_load(WORKFLOW.read_text())
    assert isinstance(parsed, dict), "ci.yml did not parse as a YAML mapping"
    assert parsed.get("name") == "ci"


def test_mutation_test_step_present() -> None:
    text = WORKFLOW.read_text()
    assert "- name: mutation-test" in text, "missing G2 step: mutation-test (PR-only, report-only)"


def test_mutation_test_step_runs_only_on_pull_request() -> None:
    """G2 is PR-only (mirrors G1b's coverage-diff-check).

    Push-to-main has no ``diff`` to evaluate against origin/main, so
    a mutmut run on a merge commit would either fail or no-op.
    """
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n"
        r"\s+if: github\.event_name == 'pull_request'\n"
        r".+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None, "mutation-test step must be gated on 'github.event_name == pull_request'"


def test_mutation_test_step_uses_patch_file_flag() -> None:
    """mutmut --use-patch-file scopes mutation to changed lines only."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "--use-patch-file" in body, (
        "mutation-test must use --use-patch-file for changed-files-only scope"
    )


def test_mutation_test_step_pipes_pytest_as_runner() -> None:
    """The config in pyproject.toml is the source of truth for the runner,
    but the inline step must reach a real exit code so non-zero from
    mutmut surfaces in the GH UI. Verify the runner CLI is configured
    in pyproject rather than inlined."""
    text = PYPROJECT.read_text()
    assert "[tool.mutmut]" in text, "pyproject.toml must contain [tool.mutmut]"
    # The runner must point at our test suite.
    m = re.search(r'runner\s*=\s*"([^"]+)"', text)
    assert m is not None, "[tool.mutmut] must declare a runner string"
    assert "pytest" in m.group(1), "runner must invoke pytest"


def test_mutation_test_step_disables_complex_mutation_types() -> None:
    """Filter low-signal mutation types for bounded CI time.

    mutmut 2.5.1's valid AST mutation types are:
        operator, keyword, number, name, string, fstring, argument,
        or_test, and_test, lambdef, expr_stmt, decorator, annassign

    Per design Q2a (corrected after empirical validation against
    mutmut 2.5.1): we disable ``argument`` because every function-call
    argument becomes ``None``, producing a flood of ``AttributeError:
    'NoneType' has no attribute X`` survivors that signal nothing
    actionable about test quality.

    NOTE: mutmut's ``config_from_file`` wrapper does ``.split(",")`` at
    runtime, so the value MUST be a string (a TOML list crashes with
    ``AttributeError: 'list' object has no attribute 'split'``).
    """
    text = PYPROJECT.read_text()
    m = re.search(r'disable_mutation_types\s*=\s*"([^"]+)"', text)
    assert m is not None, (
        "[tool.mutmut] must declare disable_mutation_types as a STRING "
        "(mutmut's config_from_file wrapper does .split(','))"
    )
    fields = {f.strip() for f in m.group(1).split(",")}
    # Disable at least one low-yield mutation class to bound CI time.
    # ``argument`` is the canonical choice for our codebase (function-call
    # args are usually structural, not logic).
    low_signal_types = fields & {"argument", "fstring", "lambdef", "decorator"}
    assert low_signal_types, (
        f"must disable at least one low-signal mutation type to bound runtime; got fields={fields}"
    )


def test_mutation_test_step_does_not_use_coverage_with_patch_file() -> None:
    """mutmut 2.5.1 forbids combining --use-coverage with --use-patch-file
    (the developer added/changed lines are presumed covered — otherwise G1a's
    coverage floor would have rejected the PR). Use only --use-patch-file."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None, "mutation-test step missing"
    body = m.group(0)
    assert "--use-coverage" not in body, (
        "mutation-test must NOT pass --use-coverage: mutmut 2.5.1 forbids "
        "combining it with --use-patch-file (raises click.BadArgumentUsage)"
    )


def test_mutation_test_step_is_continue_on_error_for_first_slice() -> None:
    """Per design Q3a: first slice is measurement-only.

    ``continue-on-error: true`` means a non-zero mutmut exit code does
    not fail the CI job; instead the artifact + GH Actions summary
    surface the kill rate and survivors. Slice 2 will flip this to
    ``false`` once the baseline is understood.
    """
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "continue-on-error: true" in body, (
        "Slice 1 mutation-test must be continue-on-error: true (report-only)"
    )


def test_mutation_test_step_emits_junit_xml() -> None:
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "mutmut junitxml" in body, (
        "mutation-test must emit JUnit XML via 'mutmut junitxml' for downstream tools"
    )


def test_upload_mutation_artifacts_step_present() -> None:
    text = WORKFLOW.read_text()
    assert "- name: upload-mutation-artifacts" in text, "missing G2 step: upload-mutation-artifacts"


def test_upload_mutation_artifacts_uploads_mutation_results() -> None:
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: upload-mutation-artifacts\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None, "could not find upload-mutation-artifacts step body"
    body = m.group(0)
    assert "mutmut-junit.xml" in body, "upload-mutation-artifacts must include mutmut-junit.xml"
    assert "actions/upload-artifact@v4" in body, (
        "upload-mutation-artifacts must use actions/upload-artifact@v4"
    )


def test_upload_mutation_artifacts_runs_on_always() -> None:
    """Artifacts must be uploaded even on non-zero mutmut exit so the
    report survives for Slice-2 gate-tuning or for the G12 dashboard."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: upload-mutation-artifacts\s*\n\s+if: always\(\).+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None, (
        "upload-mutation-artifacts must use 'if: always()' so artifacts upload on mutmut failure"
    )


def test_pytest_step_remains_before_mutation_step() -> None:
    """``mutmut run`` invokes the configured runner (pytest). The full
    pytest step in CI must run first so coverage.xml + flaky-tests.json
    exist for any post-mortem on a mutmut failure."""
    text = WORKFLOW.read_text()
    pytest_idx = text.find("- name: pytest\n")
    mutation_idx = text.find("- name: mutation-test")
    assert pytest_idx > 0, "pytest step missing"
    assert mutation_idx > pytest_idx, "mutation-test must run AFTER the regular pytest step"


def test_mutation_test_uses_diff_origin_main() -> None:
    """The patch file must reflect src/ changes from this PR vs origin/main."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "diff" in body
    assert "origin/main" in body, "diff must be against origin/main"
    assert "src/" in body, "diff must scope to src/ (avoid docs/tests churn)"


def test_dev_extra_includes_mutmut() -> None:
    text = PYPROJECT.read_text()
    assert "mutmut" in text, "mutmut must be in [project.optional-dependencies.dev]"


def test_workflow_preserves_g1c_steps() -> None:
    """All G1a / G1b / G1c steps must remain after the G2 addition."""
    text = WORKFLOW.read_text()
    required = [
        "ruff format check",
        "ruff check",
        "mypy strict",
        "bandit",
        "pip-audit",
        "pytest",  # G1a
        "- name: flaky-test-report",  # G1c
        "- name: upload-test-artifacts",  # G1c
        "- name: coverage-diff-check",  # G1b
        "- name: coverage-summary",  # G1b
        "- name: coverage-diff-summary",  # G1b
    ]
    for step in required:
        assert step in text, f"pre-existing step missing: {step}"


def test_mutation_test_diff_command_is_git_diff_with_prefix() -> None:
    """Verify the diff command uses ``git diff origin/main...HEAD``
    (three-dot) so only commits unique to this branch are considered."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "git diff origin/main...HEAD" in body, (
        "diff must use three-dot form (commits unique to PR branch)"
    )


def test_mutation_test_no_progress_flag() -> None:
    """``--no-progress`` makes mutmut's output deterministic in CI logs."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "--no-progress" in body


def test_mutation_test_simple_output_flag() -> None:
    """``--simple-output`` swaps the emoji glyphs for plain text so the
    captured log is copy-pasteable into a GH issue / PR comment."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "--simple-output" in body


def test_mutation_test_pipes_output_to_log() -> None:
    """The log must be captured to disk so the post-step summary step
    can read it. We capture into /tmp/mutmut.log."""
    text = WORKFLOW.read_text()
    m = re.search(
        r"- name: mutation-test\s*\n.+?(?=\n      - name:|\n  [a-z]:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(0)
    assert "/tmp/mutmut.log" in body, (
        "mutation-test must capture stdout+stderr to /tmp/mutmut.log for the summary step"
    )
