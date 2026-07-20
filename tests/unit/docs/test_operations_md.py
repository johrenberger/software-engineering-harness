"""I3 — Operations runbook honesty contract tests.

Story I3 — `docs/operations.md` must (a) exist, (b) link to the artifacts
it claims to describe, (c) reference the workflows it claims to triage,
(d) not promise automation that does not exist, and (e) be reachable from
the README or docs index so operators can find it.

If any of these tests fail, the runbook and the code/workflows it
references have drifted. Update one or the other deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "operations.md"
README = REPO_ROOT / "README.md"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structural commitments
# ---------------------------------------------------------------------------


def test_doc_exists() -> None:
    """The operations runbook doc must exist."""
    assert DOC_PATH.exists(), f"missing {DOC_PATH}"


def test_doc_is_substantial() -> None:
    """The runbook must be substantive — not a stub. ~5 KB minimum."""
    size = DOC_PATH.stat().st_size
    assert size >= 5_000, (
        f"docs/operations.md is only {size} bytes; a real runbook should be "
        f"at least ~5 KB. Add the missing sections."
    )


def test_doc_has_status_callout(doc: str) -> None:
    """I3 follows I1's honesty contract: a Status callout near the top."""
    # First 2 KB should include a Status block.
    head = doc[:2048]
    assert "Status" in head, (
        "docs/operations.md must open with a Status callout declaring its "
        "scope (minimal runbook vs. full ops doc set)."
    )


def test_doc_has_audience_section(doc: str) -> None:
    """An operator-facing doc must declare its audience."""
    assert re.search(r"^##\s+Audience", doc, re.MULTILINE), (
        "docs/operations.md must include an '## Audience' section explaining "
        "who the doc is for (operator vs. user)."
    )


def test_doc_has_where_artifacts_land(doc: str) -> None:
    """Core I3 commitment: a table of CI artifacts and where they land."""
    assert re.search(r"^##\s+Where artifacts land", doc, re.MULTILINE), (
        "docs/operations.md must include a '## Where artifacts land' section."
    )
    # The artifact table must mention the artifacts that actually exist.
    for artifact in (
        "junit.xml",
        "flaky-tests.json",
        "coverage.xml",
        "mutmut-junit.xml",
        "sbom-cyclonedx.json",
    ):
        assert artifact in doc, f"docs/operations.md artifact table is missing '{artifact}'"


def test_doc_has_triage_section(doc: str) -> None:
    """Operators need a CI-failure triage tree."""
    assert re.search(r"^##\s+Triage", doc, re.MULTILINE), (
        "docs/operations.md must include a '## Triage' section."
    )
    # At minimum, triage must cover CI failures and the flaky test split.
    assert "flaky" in doc.lower(), (
        "Triage section must discuss flaky tests (passed-after-reruns vs broken-tests)."
    )


def test_doc_has_maintenance_cadences(doc: str) -> None:
    """I3 commitment: explicit maintenance cadences (weekly, quarterly, etc.)."""
    assert re.search(r"^##\s+Maintenance cadences", doc, re.MULTILINE), (
        "docs/operations.md must include a '## Maintenance cadences' section."
    )
    # Must reference the cron schedules the workflows actually use.
    assert "06:00 UTC" in doc or "06:00" in doc, (
        "Maintenance cadences must mention the weekly cron times for pip-audit / Scorecard."
    )


def test_doc_links_to_release_runbook(doc: str) -> None:
    """The ops runbook and the release runbook must cross-link."""
    assert "docs/releasing.md" in doc, (
        "docs/operations.md must link to docs/releasing.md (the release "
        "runbook — different cadence, related responsibility)."
    )


def test_doc_links_to_security_md(doc: str) -> None:
    """Operator-facing docs must reference SECURITY.md for vuln disclosure."""
    assert "SECURITY.md" in doc, (
        "docs/operations.md must reference SECURITY.md so operators know "
        "where to escalate security issues."
    )


# ---------------------------------------------------------------------------
# Anti-claims: the runbook must not promise things that don't exist.
# ---------------------------------------------------------------------------


def test_doc_does_not_claim_automatic_cleanup(doc: str) -> None:
    """E3 (auto-cleanup of .openclaw-runs/) is NOT YET wired per the
    architecture overview's honesty matrix. The runbook must not claim it.

    Allowed: explicit denial (\"There is no automatic cleanup yet\", etc.).
    Forbidden: implicit claim that cleanup happens automatically.
    """
    text = doc
    # Forbidden positive claims. We look for affirmative sentences; negation
    # (\"no\", \"not\", \"yet\") within 20 chars of the trigger is permitted.
    forbidden_patterns = [
        r"automatic cleanup",
        r"auto-cleanup",
        r"automatically cleans? up",
        r"automatic rotation",
    ]
    negation_window = 20  # chars before the match
    for pat in forbidden_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            preceding = text[max(0, m.start() - negation_window) : m.start()].lower()
            if re.search(r"\b(no|not|none|yet|never)\b", preceding):
                continue  # Allowed: explicit denial
            raise AssertionError(
                f"docs/operations.md must not claim '{pat}' exists without "
                f"an explicit denial; E3 (automatic .openclaw-runs cleanup) "
                f"is in the honesty matrix as NOT YET wired."
            )


def test_doc_does_not_claim_public_dashboard_bind(doc: str) -> None:
    """Public dashboard bind (self-hosted server mode) is explicitly NOT
    doing per the README. The runbook must not contradict."""
    # The runbook links to the dashboard URL, which is fine (it's the
    # GitHub Pages published dashboard, not a self-hosted bind).
    # It must NOT claim the dashboard is publicly bindable from any
    # machine or that it's a self-hosted server.
    text = doc.lower()
    assert (
        "self-hosted server" not in text or "not " in text.split("self-hosted server")[1][:200]
    ), (
        "docs/operations.md must not claim the dashboard is a self-hosted "
        "server (it's GitHub-Pages-only by design)."
    )


def test_doc_does_not_claim_pypi_install_works(doc: str) -> None:
    """PyPI publish (G18) is not yet done. The runbook must not claim
    `pip install seharness` works without a caveat."""
    text = doc
    # The doc may mention pip install (in the context of user docs),
    # but if it does, it must also flag the G18 follow-up.
    if "pip install seharness" in text or "pip install -e" in text:
        assert (
            re.search(
                r"pip install seharness.*?not yet|G18|not.*?published",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            or "G18" in text
        ), (
            "If docs/operations.md references `pip install seharness`, it "
            "must mention G18 (the PyPI publish story) or the 'not yet "
            "published' caveat from the README honesty contract."
        )


# ---------------------------------------------------------------------------
# Cross-reference integrity: every workflow the doc references must exist.
# ---------------------------------------------------------------------------


def test_referenced_workflows_exist(doc: str) -> None:
    """Every `.github/workflows/X.yml` referenced in the doc must exist."""
    referenced = set(re.findall(r"\.github/workflows/(\w[\w-]*\.yml)", doc))
    assert referenced, "docs/operations.md should reference at least one workflow file"
    for wf in referenced:
        path = WORKFLOWS_DIR / wf
        assert path.exists(), (
            f"docs/operations.md references '.github/workflows/{wf}' but that file does not exist."
        )


def test_referenced_artifact_paths_exist(doc: str) -> None:
    """The artifact table claims specific files are uploaded; spot-check
    the source workflow mentions them."""
    # If the doc claims coverage.xml is an artifact, ci.yml must
    # upload it. We don't re-parse YAML — just grep.
    if "coverage.xml" in doc:
        ci_yml = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
        assert "coverage.xml" in ci_yml, (
            "docs/operations.md claims coverage.xml is an artifact but "
            ".github/workflows/ci.yml does not mention it."
        )
    if "junit.xml" in doc:
        ci_yml = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
        assert "junit.xml" in ci_yml, (
            "docs/operations.md claims junit.xml is an artifact but "
            ".github/workflows/ci.yml does not mention it."
        )
    if "flaky-tests.json" in doc:
        ci_yml = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
        assert "flaky-tests.json" in ci_yml, (
            "docs/operations.md claims flaky-tests.json is an artifact but "
            ".github/workflows/ci.yml does not mention it."
        )


# ---------------------------------------------------------------------------
# Discoverability: the README or docs index must link to the runbook.
# ---------------------------------------------------------------------------


def test_readme_links_to_operations_doc() -> None:
    """The README must surface the operations runbook for operators to find."""
    readme = README.read_text(encoding="utf-8")
    # Either a direct link or a mention of "operations" / "runbook" in the
    # body / user-docs section.
    assert (
        "docs/operations.md" in readme
        or "operations runbook" in readme.lower()
        or "[operations]" in readme.lower()
    ), (
        "README.md must link to docs/operations.md (or call out the "
        "operations runbook by name) so operators can find it."
    )
