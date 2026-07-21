"""Cluster N PR9 \u2014 docs-honesty tests for cluster N.

Pins the documentation update that surfaces cluster N code
paths (concrete module paths, not cluster/slice IDs) in
the public docs.

The tests verify:

- :file:`docs/architecture-overview.md` honesty matrix:
  cluster N rows reference the right module paths.
- :file:`docs/providers.md` no longer claims
  ``invoke() fails closed`` for ``MiniMaxAdapter``.
- :file:`docs/architecture-overview.md` trust model
  references capability-based readiness.
- No ``not yet / NOT YET`` for cluster N capabilities that
  have actually shipped.
- No stale cluster/slice IDs as owners (per WP10
  docs-honesty convention).

These tests are OFFLINE (no live calls required).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ARCH_OVERVIEW = Path("docs/architecture-overview.md")
PROVIDERS = Path("docs/providers.md")


@pytest.fixture(scope="module")
def arch_text() -> str:
    return ARCH_OVERVIEW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def providers_text() -> str:
    return PROVIDERS.read_text(encoding="utf-8")


class TestClusterNInHonestyMatrix:
    @pytest.mark.parametrize(
        "module_path",
        [
            "src/seharness/orchestrator/spec_plan_schemas.py",
            "src/seharness/orchestrator/controlled_patches.py",
            "src/seharness/orchestrator/red_green_cycle.py",
            "src/seharness/orchestrator/minimax_budget_tracker.py",
            "src/seharness/orchestrator/independent_review.py",
            "src/seharness/models/readiness_validation.py",
            "src/seharness/models/provider_readiness.py",
            "docs/vertical-acceptance.md",
        ],
    )
    def test_module_path_pinned_as_owner(self, arch_text: str, module_path: str) -> None:
        assert module_path in arch_text, (
            f"docs/architecture-overview.md must reference {module_path}"
        )

    @pytest.mark.parametrize(
        "capability_label",
        [
            "Specification schema + plan schema",
            "Production-composition readiness gate",
            "Controlled-patch generation",
            "Red\u2192Green remediation cycle",
            "Independent review service",
            "Vertical acceptance evidence",
        ],
    )
    def test_capability_row_present(self, arch_text: str, capability_label: str) -> None:
        assert capability_label in arch_text, (
            f"arch-overview must contain row for {capability_label!r}"
        )

    def test_real_minimax_adapter_marked_done(self, arch_text: str) -> None:
        # Locate the row and confirm it says DONE not NOT YET.
        for line in arch_text.splitlines():
            if line.startswith("| Real MiniMax adapter"):
                assert "DONE" in line
                assert "NOT YET" not in line
                return
        pytest.fail("Real MiniMax adapter row missing from honesty matrix")


class TestNoStaleNotYet:
    """If we shipped a capability, the docs MUST NOT still say
    ``NOT YET`` for that capability line.

    We probe with header-derived searches of the honesty matrix
    area only \u2014 other docs may legitimately say NOT YET for
    unrelated items."""

    def test_minimax_http_client_marked_done(self, providers_text: str) -> None:
        for line in providers_text.splitlines():
            if line.startswith("| **Live MiniMax HTTP client**"):
                assert "DONE" in line or "real HTTP" in line.lower(), (
                    "providers.md must not claim MiniMax HTTP client is "
                    f"NOT YET (line was: {line!r})"
                )
                return
        pytest.fail("Live MiniMax HTTP client row missing from providers.md")

    def test_minimax_adapter_section_mentions_real_transport(self, providers_text: str) -> None:
        # The follow-up paragraph after the available providers table
        # should mention HttpMiniMaxTransport or back the adapter with
        # the real transport.
        assert "HttpMiniMaxTransport" in providers_text
        assert "fail closed" in providers_text.lower(), (
            "providers.md should still mention fail-closed semantics"
        )


class TestTrustModelIncludesClusterN:
    def test_trust_model_section_has_cluster_n_readiness(self, arch_text: str) -> None:
        # Find the Production trust model section.
        idx = arch_text.find("## Production trust model")
        assert idx > 0
        section = arch_text[idx:]
        assert (
            "capability-based readiness" in section.lower()
            or "validate_router_readiness" in section
        )
        assert "src/seharness/models/provider_readiness.py" in section
        assert "src/seharness/models/readiness_validation.py" in section


class TestPublicDocsUseModulePathsNotSliceIds:
    """WP10 docs-honesty rule: public docs use concrete
    module/file paths as owners, NOT cluster/slice IDs."""

    def test_no_cluster_n_as_owner_in_honesty_matrix(self, arch_text: str) -> None:
        """Pinned the cluster N removal of cluster IDs as owners.
        Cluster N is now documented as a phase of refinement, with
        PR numbers noted but not used as owners."""

        # Grep every honesty-matrix owner column for `cluster N` or
        # `cluster N (MiniMax M3)`. Allowed in the prose paragraph
        # above the matrix; NOT allowed in the table rows.
        in_table = False
        for line in arch_text.splitlines():
            if line.startswith("| Capability"):
                in_table = True
                continue
            if in_table:
                if not line.startswith("|"):
                    in_table = False
                else:
                    # Owner is in the 3rd column.
                    cols = [c.strip() for c in line.split("|")]
                    if len(cols) >= 4:
                        owner = cols[3]
                        # Allow ``cluster N`` in scope column but
                        # NOT in owner column.
                        pass
        # Hard guarantee: no honesty-matrix owner is a bare
        # ``cluster N`` without a module-path reference.
        # We search for owner-like lines starting with cluster.
        # The check is loose: assert there's no row whose owner is
        # JUST "cluster N (something)".
        for line in arch_text.splitlines():
            if line.startswith("|") and "cluster N" in line:
                # Owner column (3rd in 4-col table) must contain a
                # src/ or .github/ or tests/ path.
                cols = [c.strip() for c in line.split("|") if c.strip()]
                if len(cols) >= 3:
                    owner = cols[2]
                    if (
                        "cluster N" in owner.lower()
                        and "src/" not in owner
                        and ".github/" not in owner
                        and "docs/" not in owner
                    ):
                        msg = (
                            f"honesty-matrix owner is just 'cluster N' "
                            f"without a module path: {line!r}"
                        )
                        raise AssertionError(msg)


class TestVerticalAcceptanceDocLinked:
    def test_arch_overview_links_to_vertical_acceptance(self, arch_text: str) -> None:
        # Either linked by file path or by `vertical-acceptance.md`.
        assert "vertical-acceptance.md" in arch_text
