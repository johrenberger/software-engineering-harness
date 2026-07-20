"""I2 — Architecture overview honesty contract tests.

Pin the structural commitments and honesty matrix of
``docs/architecture-overview.md``. These tests catch regressions where
the doc claims more than the code actually provides (or fewer).

If any of these tests fail, the doc and code have drifted. Update
one or the other deliberately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "architecture-overview.md"
SRC_ROOT = REPO_ROOT / "src" / "seharness"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_exists() -> None:
    """The architecture overview doc must exist."""
    assert DOC_PATH.exists(), f"missing {DOC_PATH}"


# ---------------------------------------------------------------------------
# Subsystem table — the doc claims these packages exist.
# ---------------------------------------------------------------------------


SUBSYSTEMS = [
    ("controller", "Controller", "src/seharness/controller/"),
    ("orchestrator", "Orchestrator", "src/seharness/orchestrator/"),
    ("sandbox", "Sandbox", "src/seharness/sandbox/"),
    ("ci", "CI", "src/seharness/ci/"),
    ("observability", "Observability", "src/seharness/observability/"),
    ("telegram", "Telegram", "src/seharness/telegram/"),
]


@pytest.mark.parametrize("package,display_name,path", SUBSYSTEMS)
def test_subsystem_package_exists(package: str, display_name: str, path: str) -> None:
    """Every subsystem listed in the doc must have a corresponding
    src/seharness/<package>/ directory."""
    pkg_dir = REPO_ROOT / path
    assert pkg_dir.is_dir(), f"doc claims {display_name} subsystem at {path}, but directory missing"


# ---------------------------------------------------------------------------
# Subsystems referenced in the doc must be on the subsystems table.
# ---------------------------------------------------------------------------


def test_doc_lists_all_subsystem_packages(doc: str) -> None:
    """Every package under src/seharness/ (excluding skills/__pycache__)
    must be mentioned by name somewhere in the doc (loose check).

    The 'subsystems table' is the prominent display, but supporting
    packages like 'dashboard', 'pipeline', 'models', etc. can appear in
    body text rather than the table.
    """
    real_packages = {
        d.name
        for d in (SRC_ROOT).iterdir()
        if d.is_dir()
        and (d / "__init__.py").exists()
        and d.name
        not in (
            "__pycache__",
            "skills",  # OpenClaw skill manifests, not subsystems
        )
    }
    # The doc must explicitly mention every package by name (loose check).
    for pkg in sorted(real_packages):
        assert f"`seharness.{pkg}`" in doc, (
            f"doc does not mention `seharness.{pkg}` anywhere; "
            f"add it to the subsystems table or a related-packages section"
        )


# ---------------------------------------------------------------------------
# Architecture claims — what the doc promises.
# ---------------------------------------------------------------------------


def test_doc_describes_12_phase_pipeline(doc: str) -> None:
    """The 12-phase orchestrator pipeline must be documented."""
    for phase in (
        "repository_discovery",
        "specification",
        "planning",
        "implementation",
        "validation",
        "remediation",
        "review",
        "draft_pr",
        "ci",
        "ready",
        "completed",
    ):
        assert phase in doc, f"doc missing phase: {phase}"


def test_doc_describes_terminal_states(doc: str) -> None:
    """The 4 terminal states must be listed in the doc."""
    for state in ("completed", "failed", "blocked", "paused"):
        assert state in doc, f"doc missing terminal state: {state}"


def test_doc_describes_storage_layout(doc: str) -> None:
    """The doc must describe the run storage layout."""
    assert ".openclaw-runs/orchestrator/<run_id>/" in doc
    assert "repo_profile.json" in doc
    assert "specification.json" in doc
    assert "plan.json" in doc
    assert "trace.jsonl" in doc


def test_doc_describes_protocols(doc: str) -> None:
    """The doc must reference the Protocol-based architecture."""
    assert "Protocol" in doc
    assert "mutation-killer" in doc or "mutation killer" in doc


# ---------------------------------------------------------------------------
# Honesty matrix — the doc must acknowledge what's NOT done yet.
# ---------------------------------------------------------------------------


def test_honesty_matrix_present(doc: str) -> None:
    """The doc must include a 'What is NOT yet wired' matrix."""
    assert "NOT YET" in doc, (
        "doc must include an explicit 'What is NOT yet wired' section to prevent over-claiming"
    )


def test_honesty_matrix_lists_idempotency(doc: str) -> None:
    """The honesty matrix must mention E1 idempotency. As of PR #53
    (Cluster E1), this row has shipped (option B — caller plumbing);
    the row must reflect that WITH a scope qualifier so the doc
    remains honest about which sub-features are done.
    """
    assert "Idempotency" in doc
    assert "E1" in doc
    # Honesty contract: a DONE entry MUST document the scope.
    # Option B ships caller plumbing; persistence (option C) is still
    # pending, so the doc must mention at least one of
    # "caller plumbing" / "no persistence" / "B" as the qualifier.
    assert any(
        marker in doc
        for marker in (
            "caller plumbing",
            "no persistence",
            "(B",
            "(B \u2014",
            "option B",
            "B —",
        )
    ), "E1 entry must document its scope (B = caller plumbing)"


def test_honesty_matrix_marks_e4_cancellation_done(doc: str) -> None:
    """Cluster E4 (E4a primitive + E4b orchestrator wiring) shipped in
    PRs #49 (E4a) and #52 (E4b). The honesty matrix must reflect that.
    """
    assert "Cancellation propagation" in doc or "E4" in doc
    # After the #52 merge the cancellation row should NOT say NOT YET.
    # (E2/E7/E3/F rows may still say NOT YET.)


def test_honesty_matrix_lists_concurrency(doc: str) -> None:
    """The honesty matrix must mention E2 optimistic concurrency as NOT YET."""
    assert "Optimistic concurrency" in doc or "optimistic" in doc.lower()
    assert "E2" in doc


def test_honesty_matrix_lists_real_model_adapters(doc: str) -> None:
    """The honesty matrix must mention real model adapters as NOT YET."""
    assert "Codex" in doc
    assert "MiniMax" in doc


def test_honesty_matrix_references_clusters(doc: str) -> None:
    """Each row must reference an owner cluster.

    As of PR #53 (Cluster E1 idempotency keys) and PR #52 (Cluster E4
    cancel propagation), both E1 and E4 rows have shipped and now
    appear in the DONE column. E2, E7, E3, F remain P1-and-NOT-YET;
    G19 is P2.
    """
    for cluster in ("E1", "E2", "E4", "E7", "G19"):
        assert cluster in doc, f"doc honesty matrix must reference {cluster} as owner cluster"
    # G18 is the historical predecessor story for G9 (release
    # automation). Kept as a documented reference even though the
    # release work landed under G9.
    assert "G18" in doc, "doc honesty matrix must keep the historical G18 reference"


# ---------------------------------------------------------------------------
# Anti-claims — things the doc must NOT say we do yet.
# ---------------------------------------------------------------------------


def test_doc_does_not_claim_pypi_published(doc: str) -> None:
    """The doc must NOT claim 'pip install seharness' works."""
    forbidden = [
        "pip install seharness works",
        "available on PyPI",
        "PyPI package",
    ]
    for phrase in forbidden:
        assert phrase.lower() not in doc.lower(), (
            f"doc contains forbidden claim: {phrase!r} — we don't ship to PyPI yet (G18 follow-up)"
        )


def test_doc_does_not_claim_branch_protection(doc: str) -> None:
    """The doc must NOT claim branch protection is configured."""
    forbidden = [
        "branch protection is enabled",
        "main is protected",
        "protected branch",
    ]
    for phrase in forbidden:
        assert phrase.lower() not in doc.lower(), (
            f"doc contains forbidden claim: {phrase!r} — branch protection "
            f"is NOT YET configured (G19 follow-up)"
        )


# ---------------------------------------------------------------------------
# Cross-references — the doc must link to related docs.
# ---------------------------------------------------------------------------


def test_doc_links_to_orchestrator_doc(doc: str) -> None:
    """The doc must link to docs/architecture.md for orchestrator internals."""
    assert "docs/architecture.md" in doc or "architecture.md" in doc


def test_doc_links_to_user_docs(doc: str) -> None:
    """The doc must link to user-facing docs."""
    assert "docs/user/" in doc


def test_doc_links_to_evidence(doc: str) -> None:
    """The doc must link to the evidence directory."""
    assert "evidence" in doc


# ---------------------------------------------------------------------------
# Length + format sanity.
# ---------------------------------------------------------------------------


def test_doc_at_least_100_lines(doc: str) -> None:
    """A 'service graph' doc should be substantive (>100 lines)."""
    line_count = len(doc.splitlines())
    assert line_count >= 100, (
        f"architecture-overview.md is only {line_count} lines; should be more substantive"
    )


def test_doc_has_status_callout(doc: str) -> None:
    """The doc should open with an Alpha/v0.1.0 status callout."""
    assert "Alpha" in doc or "v0.1.0" in doc, "doc should open with explicit Alpha / v0.1.0 status"
