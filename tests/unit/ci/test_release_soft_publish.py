"""Cluster G9 / Tier 4d: PyPI publish is best-effort.

Contract: the `publish-pypi` job in ``.github/workflows/release.yml``
must NOT block the GitHub Release. If PyPI is unreachable / untrusted /
the GitHub ``pypi`` environment isn't configured, the workflow logs
the outcome and ships the release anyway.

These tests fail loudly if someone reintroduces hard-fail semantics.

Background (2026-07-21): before this contract was added, the workflow
declared::

    github-release:
      needs: [build, publish-pypi]

which meant a missing PyPI Trusted Publisher or unconfigured GitHub
``pypi`` environment would cancel the entire release. That defeats
the operator model "tag = release".
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def release() -> dict:
    """Parsed ``release.yml`` as a dict."""
    return yaml.safe_load(RELEASE_YML.read_text())


def _jobs(release: dict) -> dict[str, dict]:
    return release["jobs"]


# ---------------------------------------------------------------------------
# 1. publish-pypi must be best-effort
# ---------------------------------------------------------------------------


def test_publish_pypi_declared() -> None:
    """The job exists. (Sanity — the others assume it's there.)"""
    wf = yaml.safe_load(RELEASE_YML.read_text())
    assert "publish-pypi" in wf["jobs"], "release.yml must declare a publish-pypi job"


def test_publish_steps_carry_continue_on_error(release: dict) -> None:
    """Both ``pypa/gh-action-pypi-publish`` invocations (TestPyPI and
    PyPI prod) MUST be ``continue-on-error: true``. Otherwise a 403
    from PyPI cancels the entire release.
    """
    job = _jobs(release)["publish-pypi"]
    publish_steps = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish")
    ]
    assert len(publish_steps) == 2, (
        f"Expected exactly 2 pypi-publish steps (TestPyPI + PyPI); "
        f"got {len(publish_steps)}. Did someone remove one?"
    )
    for step in publish_steps:
        assert step.get("continue-on-error") is True, (
            f"Step '{step.get('name')}' must set continue-on-error: true "
            f"so a missing Trusted Publisher logs a warning instead of "
            f"cancelling the release. Got: {step!r}"
        )


def test_publish_pypi_records_status_in_step_summary(release: dict) -> None:
    """A final ``always()`` step must record the publish outcome so
    operators can see what happened without scraping job logs.
    """
    job = _jobs(release)["publish-pypi"]
    status_steps = [
        step
        for step in job["steps"]
        if "status" in step.get("name", "").lower() and step.get("if") == "always()"
    ]
    assert status_steps, (
        "publish-pypi must end with an `if: always()` step that records "
        "the outcome to $GITHUB_STEP_SUMMARY (or similar). Otherwise "
        "operators have no in-UI signal that publish was skipped."
    )
    body = status_steps[0]["run"]
    assert "GITHUB_STEP_SUMMARY" in body
    # Must mention both "published" and "NOT PUBLISHED" branches so the
    # log distinguishes success from soft-skip.
    assert "published" in body
    assert "NOT PUBLISHED" in body


# ---------------------------------------------------------------------------
# 2. github-release must NOT depend on publish-pypi
# ---------------------------------------------------------------------------


def test_github_release_does_not_depend_on_publish_pypi(release: dict) -> None:
    """``github-release`` MUST only need ``build``. If anyone re-adds
    ``publish-pypi`` to the needs chain, a publish failure cancels
    the release — which violates the soft-publish contract.
    """
    job = _jobs(release)["github-release"]
    needs = job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    needs_list = list(needs)
    assert "publish-pypi" not in needs_list, (
        f"github-release must NOT depend on publish-pypi (best-effort). "
        f"Got needs={needs_list!r}. Remove publish-pypi from the chain "
        f"so a missing PyPI Trusted Publisher logs but doesn't cancel "
        f"the release."
    )
    assert "build" in needs_list, (
        f"github-release must still wait for `build`. Got needs={needs_list!r}."
    )


def test_github_release_runs_on_tag_push(release: dict) -> None:
    """Belt-and-braces: the github-release job must still gate on tag
    pushes (not PR builds) so PRs don't accidentally publish a draft.
    """
    job = _jobs(release)["github-release"]
    assert "startsWith(github.ref, 'refs/tags/v')" in job["if"], (
        f"github-release must be gated on tag pushes (refs/tags/v*). Got if={job['if']!r}."
    )


# ---------------------------------------------------------------------------
# 3. soft-publish policy is documented in the workflow file
# ---------------------------------------------------------------------------


def test_workflow_documents_soft_publish_policy() -> None:
    """The workflow header comment MUST explain the best-effort policy.

    Without this, future maintainers will assume the publish-pypi
    failure is a bug and "fix" it by re-adding ``needs: [build,
    publish-pypi]`` to github-release.
    """
    text = RELEASE_YML.read_text()
    assert "BEST-EFFORT" in text or "best-effort" in text, (
        "release.yml must document that PyPI publish is best-effort "
        "(look for 'BEST-EFFORT' or 'best-effort' in the header comment)."
    )
    assert "GitHub Release still ships" in text or "release still ships" in text, (
        "release.yml must explain that the GitHub Release ships even if PyPI publish fails."
    )
