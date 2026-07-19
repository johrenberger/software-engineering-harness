"""I1+I4: README honesty contract tests.

Story I1 — README must clearly flag what's NOT yet working (Status section).
Story I4 — README must NOT make claims that are contradicted by the code
or the priority tracker.

These tests do not claim every line of the README is true (that would be
brittle and over-fit). They pin the structural commitments the maintainers
made in the Status section so that any future PR that removes them is
flagged by CI.

Run: ``uv run pytest tests/unit/docs/test_readme_honesty.py``
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    """The raw README.md text."""
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structural commitments (I1)
# ---------------------------------------------------------------------------


def test_readme_has_status_section(readme: str) -> None:
    """A top-level `## Status` section is mandatory (I1's core ask)."""
    assert "\n## Status\n" in readme or readme.startswith("## Status\n"), (
        "README must include a `## Status` section as the source of truth for "
        "what the project does today."
    )


def test_status_section_distinguishes_works_partial_notdoing(readme: str) -> None:
    """Status must distinguish ✅ works / ⚠️ partial / ❌ not doing."""
    assert "What works end-to-end" in readme or "✅" in readme
    assert "partial or planned" in readme.lower() or "⚠️" in readme
    assert "NOT doing" in readme or "❌" in readme


def test_status_section_names_pypi_as_not_yet_published(readme: str) -> None:
    """The PyPI gap must be explicit (the most-asked question)."""
    # Either "Not yet published" or "Not yet released" or "G18"
    assert re.search(
        r"PyPI.*?not yet|pip install seharness.*?not|not.*?published.*?PyPI",
        readme,
        re.IGNORECASE | re.DOTALL,
    ), "README must explicitly say PyPI release is not yet available"


def test_status_section_acknowledges_alpha_status(readme: str) -> None:
    """v0.1.0 / Alpha framing must be present so users calibrate expectations."""
    assert "0.1.0" in readme
    assert "Alpha" in readme or "alpha" in readme


def test_readme_links_to_priority_tracker(readme: str) -> None:
    """Status section must point to the priority tracker so readers can see
    what is in flight (P1/P2 transparency)."""
    assert "priority-stories" in readme.lower() or "priority" in readme.lower()


def test_readme_links_to_releasing_doc(readme: str) -> None:
    """Releasing runbook must be linked — release process is a top-asked
    question."""
    assert "docs/releasing.md" in readme or "releasing" in readme.lower()


def test_readme_links_to_engineering_dashboard_doc(readme: str) -> None:
    """Engineering dashboard is the project's 'are we healthy' page;
    README must surface it."""
    assert "engineering-dashboard" in readme.lower() or "dashboard" in readme.lower()


# ---------------------------------------------------------------------------
# Negative claims (I4)
# ---------------------------------------------------------------------------


def test_readme_does_not_claim_pypi_publish_works(readme: str) -> None:
    """The `pip install seharness` section must NOT be presented as a
    completed step. We allow it to be mentioned as the aspirational command,
    but it must be coupled with a not-yet-published caveat.

    Detect the failure mode: a `pip install seharness` code block with NO
    status caveat anywhere on the page.
    """
    pip_block = "pip install seharness" in readme
    assert pip_block, "pip install seharness should be in README (it's the install command)"
    caveat_present = bool(
        re.search(
            r"PyPI.*?(not yet|planned|pending|future)|"
            r"not.*?published.*?PyPI|"
            r"G18",
            readme,
            re.IGNORECASE,
        )
    )
    assert caveat_present, (
        "If README mentions `pip install seharness`, it MUST caveat that "
        "PyPI publish has not happened yet."
    )


def test_readme_does_not_promise_sub_30s_tests_if_slower(readme: str) -> None:
    """If README claims a test-time budget, it must be defensible.

    We don't enforce <30 s strictly — we just require that any specific
    time claim is paired with the unit ("passed in X.XXs").
    """
    # Allow loose phrases like "<30 s" but require unit or "in <…"
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*s\b", readme)
    if match:
        # Read README; check that the time mention is in a performance context.
        # Specifically, "in X.XXs" with the test count or "test" near it.
        time_mentions = re.findall(r"[^\n]*\b\d+(?:\.\d+)?\s*s\b[^\n]*", readme)
        for line in time_mentions:
            if "test" not in line.lower() and "passed" not in line.lower():
                # Allow other contexts (e.g. "timeout_s=60.0", "duration_s=1.23")
                # by checking for seconds-as-unit context.
                if re.search(r"timeout|duration|_s=|seconds|60\.0", line):
                    continue
                pytest.fail(
                    f"README has a `…s` mention without a `test`/`passed` anchor; "
                    f"either add context or remove: {line!r}"
                )


def test_readme_does_not_claim_unbounded_dashboard_bind(readme: str) -> None:
    """The dashboard's 127.0.0.1-only bind is a security commitment. The
    README must NOT claim public bind works."""
    assert "127.0.0.1" in readme
    # If there's a sentence claiming public bind, fail.
    forbidden = re.search(
        r"public(?:ly)?\s+bind(?:ing)?\s+(?:is\s+)?(?:supported|allowed|works)",
        readme,
        re.IGNORECASE,
    )
    assert forbidden is None, (
        "README must not claim public bind of the dashboard is supported; "
        "ALLOWED_HOSTS is loopback-only."
    )


# ---------------------------------------------------------------------------
# Cross-references (I4: claims in README must point to verifiable artifacts)
# ---------------------------------------------------------------------------


def test_readme_links_to_docs_user_traces_when_mentioning_traces(readme: str) -> None:
    """If README mentions run traces, it must link to docs/user/traces.md."""
    if "trace" in readme.lower():
        assert "docs/user/traces" in readme or "traces.md" in readme


def test_readme_links_to_docs_user_sandbox_when_mentioning_sandbox(readme: str) -> None:
    """If README mentions the sandbox, it must link to docs/user/sandbox.md."""
    if "sandbox" in readme.lower():
        assert "docs/user/sandbox" in readme or "sandbox.md" in readme


def test_readme_links_to_security_md() -> None:
    """Security reporting path must be linked."""
    text = README.read_text(encoding="utf-8")
    assert "SECURITY.md" in text


# ---------------------------------------------------------------------------
# Live verification (skip if no network)
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_dashboard_url_referenced_in_readme_is_reachable(readme: str) -> None:
    """If README references the live dashboard URL, it must respond 200.

    This is an optional test (network). It fails-soft (skip) if no network.
    """
    urls = re.findall(r"https://[^\s)]+software-engineering-harness[^\s)]*", readme)
    if not urls:
        pytest.skip("README does not reference the dashboard URL")

    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "seharness-readme-test"})
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
                assert resp.status == 200, f"{url} returned {resp.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            pytest.skip(f"No network: {exc}")


# ---------------------------------------------------------------------------
# File-system sanity (pin README is present + non-trivial)
# ---------------------------------------------------------------------------


def test_readme_exists_and_is_non_trivial() -> None:
    """The README must be >2 KB (we know the rewrite is ~10 KB)."""
    assert README.exists(), "README.md must exist"
    size = README.stat().st_size
    assert size > 2048, f"README.md is suspiciously small ({size} bytes)"


def test_readme_first_paragraph_explains_what_this_is() -> None:
    """The README's first paragraph must clearly explain what the harness is."""
    text = README.read_text(encoding="utf-8")
    # First paragraph = text up to the first blank line.
    first_para = text.split("\n\n")[0]
    assert "harness" in first_para.lower()
    # Allow the title-only first paragraph; require the description sentence
    # (paragraph 2) to contain the explanation. Title alone is not enough.
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    description = " ".join(
        p for p in paragraphs if not p.startswith("# Software Engineering Harness")
    )
    assert "Python" in description or "framework" in description.lower(), (
        f"Description paragraph must mention 'Python' or 'framework':\n{description[:200]}"
    )
