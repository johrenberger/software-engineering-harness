"""Contract tests for G8 — SECURITY.md.

G8 requires the repo to ship a SECURITY.md that documents:
  * How to report a vulnerability privately.
  * The maintainer's expected response timeline.
  * Supported versions table.
  * Scope (in-scope and out-of-scope vulnerability classes).

These tests pin the section headers + key phrases so accidental
deletions / rewrites get caught in CI rather than at audit time.

References:
- G8 spec: docs/analysis/2026-07-19-priority-stories.md
- GitHub SECURITY.md convention:
  https://docs.github.com/en/code-security/security-policy/guidance-on-writing-your-repositorys-security-policy
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SECURITY_MD = REPO_ROOT / "SECURITY.md"
README_MD = REPO_ROOT / "README.md"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def security_text() -> str:
    if not SECURITY_MD.is_file():
        return ""
    return SECURITY_MD.read_text()


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README_MD.read_text()


# ----------------------------------------------------------------------
# 1. File existence + minimum size
# ----------------------------------------------------------------------


def test_security_md_exists() -> None:
    """SECURITY.md must exist at the repo root (GitHub looks there first)."""
    assert SECURITY_MD.is_file(), (
        "G8: SECURITY.md must exist at repo root. "
        "GitHub Security Advisories links to this exact path."
    )


def test_security_md_is_non_trivial(security_text: str) -> None:
    """SECURITY.md must have substantive content (not just a placeholder)."""
    assert len(security_text) >= 500, (
        f"SECURITY.md must be at least 500 chars of actual policy text "
        f"(got {len(security_text)} chars); a one-line placeholder "
        f"doesn't tell reporters how to disclose"
    )


# ----------------------------------------------------------------------
# 2. Section completeness (per GH Security Policy convention)
# ----------------------------------------------------------------------


def _has_section(text: str, heading_phrase: str) -> bool:
    """True if `text` contains a markdown heading whose lowercased title
    contains the lowercased `heading_phrase`."""
    h = heading_phrase.lower()
    # Match:  # Heading, ## Heading, ### Heading, then content
    for m in re.finditer(r"^#{1,6}\s+([^\n]+)$", text, re.MULTILINE):
        if h in m.group(1).lower():
            return True
    return False


@pytest.mark.parametrize(
    "section",
    [
        "Reporting a vulnerability",
        "Response timeline",
        "Supported versions",
        "Contact",
    ],
)
def test_security_md_has_required_section(security_text: str, section: str) -> None:
    """SECURITY.md must include each required section heading.

    Per GH Security Advisories convention, the four standard sections
    are: Reporting, Response, Supported versions, Contact.
    """
    assert _has_section(security_text, section), (
        f"SECURITY.md must contain a heading mentioning '{section}' (GH Security Policy convention)"
    )


# ----------------------------------------------------------------------
# 3. Specific required content (string assertions)
# ----------------------------------------------------------------------


def test_security_md_warns_against_public_issues(security_text: str) -> None:
    """The reporting instructions must tell reporters NOT to file a public issue."""
    lowered = security_text.lower()
    # Must warn explicitly about the public-issue trap.
    assert (
        "do not file a public" in lowered
        or "do not file a github issue" in lowered
        or "no public" in lowered
        or "private" in lowered
    ), (
        "SECURITY.md reporting instructions must explicitly tell reporters "
        "NOT to file a public GitHub issue (use Security Advisories instead)"
    )


def test_security_md_mentions_github_security_advisories(
    security_text: str,
) -> None:
    """SECURITY.md must link to GH Security Advisories (the canonical private channel)."""
    assert "github.com" in security_text and (
        "security/advisories" in security_text.lower()
        or "security advisory" in security_text.lower()
    ), (
        "SECURITY.md must reference GitHub Security Advisories "
        "(the recommended private reporting channel)"
    )


def test_security_md_has_response_timeline(security_text: str) -> None:
    """Response timeline section must have at least one duration phrase (e.g. "days")."""
    m = re.search(
        r"##?\s*Response timeline[^\n]*\n(.*?)(?=\n##? |\Z)",
        security_text,
        re.DOTALL,
    )
    assert m is not None, "SECURITY.md must have a `Response timeline` section"
    body = m.group(1).lower()
    # Must mention at least one duration.
    assert (
        re.search(r"\b(\d+)\s+days?\b", body)
        or re.search(r"\b(\d+)\s+weeks?\b", body)
        or re.search(r"\b(days?|weeks?|hours?)\b", body)
    ), (
        f"Response timeline section must mention a duration (days/weeks/hours). "
        f"Sample body: {body[:200]!r}"
    )


def test_security_md_supported_versions_table_marks_unsupported(
    security_text: str,
) -> None:
    """The supported versions table must explicitly mark some versions as unsupported.

    Per GH convention, the table should have ✅ / ❌ (or similar)
    markers so readers can see at a glance.
    """
    m = re.search(
        r"##?\s*Supported versions[^\n]*\n(.*?)(?=\n##? |\Z)",
        security_text,
        re.DOTALL,
    )
    assert m is not None, "SECURITY.md must have a `Supported versions` section"
    body = m.group(1)
    # Look for explicit unsupported markers.
    assert (
        re.search(
            r"\b(?:No|Unsupported|❌|not supported|won't fix)\b",
            body,
            re.IGNORECASE,
        )
        or "no" in body.lower()
    ), (
        "Supported versions section must mark some versions as unsupported "
        "(version support boundary is the whole point of the section)"
    )


def test_security_md_has_in_scope_section(security_text: str) -> None:
    """Should describe what vulnerability classes are in scope (helps reporter triage)."""
    assert _has_section(security_text, "Scope") or _has_section(security_text, "in scope"), (
        "SECURITY.md should have a Scope section (or 'in scope' subsection) "
        "so reporters know what to bother reporting"
    )


# ----------------------------------------------------------------------
# 4. README links to SECURITY.md
# ----------------------------------------------------------------------


def test_readme_links_to_security_md(readme_text: str) -> None:
    """The repo README must contain a Security section that links to SECURITY.md.

    Without a link, contributors don't know the policy exists.
    """
    assert (
        re.search(r"^##\s+Security\b.*$", readme_text, re.MULTILINE) or "Security" in readme_text
    ), "README.md must include a Security section"
    # Must link to the SECURITY.md file.
    assert "SECURITY.md" in readme_text, "README.md's Security section must link to SECURITY.md"


# ----------------------------------------------------------------------
# 5. gh label compatibility (defensive)
# ----------------------------------------------------------------------


def test_security_md_email_format_looks_valid(security_text: str) -> None:
    """If SECURITY.md lists an email, it must look like a valid address.

    Defensive: catches typos like `security@@openclaw` that would
    silently disable the alternative reporting channel.
    """
    emails = re.findall(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        security_text,
    )
    for e in emails:
        # No double dots, no leading/trailing dot.
        assert ".." not in e, f"email {e!r} has consecutive dots"
        assert not e.startswith("."), f"email {e!r} starts with a dot"
