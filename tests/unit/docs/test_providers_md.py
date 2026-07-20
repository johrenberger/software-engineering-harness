"""F1 — Providers & credentials doc honesty contract tests.

Story F1 — `docs/providers.md` must (a) exist, (b) describe the
provider/credential configuration as it actually exists today, and
(c) NOT promise credential-loading features that aren't wired.

If any of these tests fail, the doc has drifted from the code or
the README's honesty matrix. Update one or the other deliberately.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "providers.md"
README = REPO_ROOT / "README.md"
CONFIG_PY = REPO_ROOT / "src" / "seharness" / "config.py"
ROUTER_PY = REPO_ROOT / "src" / "seharness" / "models" / "router.py"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structural commitments
# ---------------------------------------------------------------------------


def test_doc_exists() -> None:
    """The providers doc must exist."""
    assert DOC_PATH.exists(), f"missing {DOC_PATH}"


def test_doc_is_substantial() -> None:
    """The doc must be substantive — not a stub."""
    size = DOC_PATH.stat().st_size
    assert size >= 4_000, (
        f"docs/providers.md is only {size} bytes; a real provider-config "
        f"doc should be at least ~4 KB. Add the missing sections."
    )


def test_doc_has_status_callout(doc: str) -> None:
    """F1 follows the I1 honesty contract: a Status callout near the top."""
    head = doc[:2048]
    assert "Status" in head, (
        "docs/providers.md must open with a Status callout declaring the "
        "scope (current state vs. not-yet-wired features)."
    )


def test_doc_has_tl_dr_table(doc: str) -> None:
    """A summary table must distinguish ✅ works / ⚠️ partial / ❌ not doing."""
    assert re.search(r"\|.*?Status.*?\|", doc, re.MULTILINE), (
        "docs/providers.md must include a TL;DR table with a Status column."
    )
    # Must include all three status symbols somewhere.
    text = doc
    assert "✅" in text, "TL;DR table must include ✅ for working features"
    assert "⚠️" in text, "TL;DR table must include ⚠️ for partial features"


def test_doc_lists_known_providers(doc: str) -> None:
    """Doc must name the actual provider IDs from config.py."""
    text = doc
    assert "minimax" in text, "doc must mention the 'minimax' provider ID"
    assert "codex" in text, "doc must mention the 'codex' provider ID"


# ---------------------------------------------------------------------------
# Honesty: doc must NOT claim features that aren't wired.
# ---------------------------------------------------------------------------


def test_doc_does_not_claim_providers_toml_works(doc: str) -> None:
    """README honesty matrix says `config/providers.toml` is NOT yet
    wired. The providers doc must not contradict that."""
    text = doc
    # Forbidden: affirmative verb within ~40 chars of 'providers.toml'
    # saying it's read. Allowed: explicit denial ('not used', 'not yet').
    forbidden_patterns = [
        r"providers\.toml[^.\n]{0,40}?\bis\b[^.\n]{0,15}?\b(?:read|loaded|consulted|used|supported)\b",
    ]
    negation_window = 40
    for pat in forbidden_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            preceding = text[max(0, m.start() - negation_window) : m.start()].lower()
            if re.search(r"\b(no|not|none|yet|never)\b", preceding):
                continue  # Allowed: explicit denial
            raise AssertionError(
                f"docs/providers.md claims '{pat}' — README honesty matrix "
                f"says config/providers.toml is NOT yet wired. Update the "
                f"doc or the README's honesty table, not both."
            )


def test_doc_does_not_claim_api_key_env_var_works(doc: str) -> None:
    """No env var credential loading is wired today. Doc must not claim
    `SEHARNESS_PROVIDER_*_API_KEY` is read."""
    text = doc
    # Look for affirmative language about the env var being loaded.
    forbidden = re.compile(
        r"SEHARNESS_PROVIDER_[A-Z]+_API_KEY.*?(?:is read|is loaded|is consulted)",
        re.IGNORECASE,
    )
    matches = forbidden.findall(text)
    assert not matches, (
        f"docs/providers.md claims SEHARNESS_PROVIDER_*_API_KEY is read, "
        f"but no such env-var loading is wired in src/seharness/. "
        f"Match: {matches!r}"
    )


def test_doc_does_not_claim_live_providers_work(doc: str) -> None:
    """Both MiniMax and Codex adapters fail closed in invoke(). Doc must
    not claim they make real network or subprocess calls today."""
    text = doc.lower()
    # Forbidden: positive claim that the adapters make real calls today.
    forbidden_patterns = [
        (r"minimax.*?makes real http calls", "minimax live HTTP"),
        (r"codex.*?makes real subprocess calls", "codex live subprocess"),
        (r"minimax.*?is fully wired", "minimax fully wired"),
        (r"codex.*?is fully wired", "codex fully wired"),
    ]
    for pat, label in forbidden_patterns:
        assert not re.search(pat, text), (
            f"docs/providers.md must not claim '{label}' works today. "
            f"Both adapters fail closed (see src/seharness/models/minimax.py "
            f"and src/seharness/models/codex.py)."
        )


def test_doc_does_not_claim_credentials_are_loaded(doc: str) -> None:
    """No credential-loading code exists. Doc must not claim credentials
    are loaded anywhere."""
    text = doc.lower()
    # Allowed: \"no credentials are required\", \"no credentials are loaded\".
    # Forbidden: \"credentials are loaded from <X>\".
    forbidden = re.compile(
        r"credentials?\s+are\s+(?:loaded|read|fetched)\s+from\s+(?!config)",
    )
    matches = forbidden.findall(text)
    assert not matches, (
        f"docs/providers.md claims credentials are loaded, but no such "
        f"code path exists. Match: {matches!r}"
    )


# ---------------------------------------------------------------------------
# Cross-reference integrity: doc claims must match code reality.
# ---------------------------------------------------------------------------


def test_known_providers_match_config(doc: str) -> None:
    """The provider IDs the doc names must match _KNOWN_PROVIDERS in
    config.py (or its current equivalent)."""
    config_text = CONFIG_PY.read_text(encoding="utf-8")
    # Pull out provider IDs from _KNOWN_PROVIDERS assignment.
    m = re.search(r"_KNOWN_PROVIDERS:\s*tuple\[.*?\]\s*=\s*\(([^)]+)\)", config_text)
    assert m is not None, "could not parse _KNOWN_PROVIDERS from config.py"
    declared = {
        token.strip().strip('"').strip("'").split(".")[-1].lower()
        for token in m.group(1).split(",")
    }
    assert declared, "_KNOWN_PROVIDERS appears empty"
    doc_text = doc.lower()
    for p in declared:
        assert p in doc_text, (
            f"config.py declares provider '{p}' but docs/providers.md does not mention it."
        )


def test_default_routing_table_matches_router(doc: str) -> None:
    """The default routing table the doc shows must match DEFAULT_ROUTING
    in router.py (both as published values)."""
    router_text = ROUTER_PY.read_text(encoding="utf-8")
    # Each role-to-provider mapping must appear in both files.
    # Hardcode the four known slots; if router.py changes them, the
    # test will catch drift.
    expected = {
        ("PLANNING", "minimax"),
        ("IMPLEMENTATION", "codex"),
        ("REMEDIATION", "codex"),
        ("REVIEW", "minimax"),
        ("DELIVERY", "minimax"),
    }
    doc_text = doc
    for role_lit, provider in expected:
        # The doc shows them in lowercase YAML form: `planning: minimax`.
        yaml_form = f"{role_lit.lower()}: {provider}"
        assert yaml_form in doc_text, f"docs/providers.md default routing missing '{yaml_form}'"
        # The router file references the role literal.
        assert role_lit in router_text, f"router.py missing role literal '{role_lit}'"
        # And the provider name in lowercase.
        assert provider in router_text.lower(), f"router.py missing provider '{provider}'"


def test_fallback_table_matches_router(doc: str) -> None:
    """The fallback table the doc shows must match DEFAULT_FALLBACK
    in router.py."""
    router_text = ROUTER_PY.read_text(encoding="utf-8")
    doc_text = doc
    # Both mappings must appear.
    assert "minimax: codex" in doc_text, "docs/providers.md fallback table missing 'minimax: codex'"
    assert "codex: minimax" in doc_text, "docs/providers.md fallback table missing 'codex: minimax'"
    # And in router.py (case-insensitive on provider names).
    rt_lower = router_text.lower()
    assert "minimax" in rt_lower and "codex" in rt_lower, (
        "router.py missing one of the providers in DEFAULT_FALLBACK"
    )


# ---------------------------------------------------------------------------
# Discoverability: README must link to the doc.
# ---------------------------------------------------------------------------


def test_readme_links_to_providers_doc() -> None:
    """The README must surface the providers doc for operators to find."""
    readme = README.read_text(encoding="utf-8")
    assert (
        "docs/providers.md" in readme
        or "providers doc" in readme.lower()
        or "[providers]" in readme.lower()
    ), (
        "README.md must link to docs/providers.md (or call out the "
        "providers doc by name) so users can find it."
    )
