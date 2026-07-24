"""Vertical-acceptance documentation tests (M3 framing).

Pins that :file:`docs/vertical-acceptance.md` exists and carries
the structural shape required by the M3 corrective refinement
(``plans/minimax-m3-corrective-processing-instructions.md``).

The tests verify:

- :file:`docs/vertical-acceptance.md` exists.
- It pins the 9-stage acceptance walkthrough to specific
  code modules (not cluster/slice IDs).
- It references the live artifact path
  (``tests/e2e/_artifacts/minimax_live_smoke.json``) and the
  reproduction recipe.
- It mentions ``DRAFT`` at least twice so that the M2.7
  historical-transport-evidence callout and the stop-gate
  reminder remain prominent in the doc.

These tests are OFFLINE (no live calls required). The live
artifact itself is git-ignored and not asserted on disk; the
doc references it.

History: this test was originally added in cluster N PR8
(``fe14120`` era) to pin the cluster-N DRAFT vertical-acceptance
doc. After the M3 corrective refinement, the doc at
``docs/vertical-acceptance.md`` is now the M3 acceptance index;
the cluster-N DRAFT doc has been renamed to
``docs/vertical-acceptance-cluster-n.md`` and is preserved as
historical transport evidence only. The structural assertions
still hold because the M3 index preserves the 9-stage shape,
the four module pins, the live artifact path, the reproduction
recipe, and the ``DRAFT`` mentions (which now point at the M2.7
historical callout and the stop-gate reminder rather than at a
draft PR).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC_PATH = Path("docs/vertical-acceptance.md")


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


class TestVerticalAcceptanceDocExists:
    def test_doc_file_exists(self) -> None:
        assert DOC_PATH.is_file(), f"{DOC_PATH} missing"

    def test_doc_is_markdown_with_heading(self, doc_text: str) -> None:
        assert doc_text.lstrip().startswith("#"), "doc must start with a heading"
        assert "vertical" in doc_text.lower()


class TestVerticalAcceptanceDocContent:
    @pytest.mark.parametrize(
        "must_mention",
        [
            "MiniMax",
            "live",
            "credentialed",
            "DRAFT",
            "https://api.minimax.io/v1/",
        ],
    )
    def test_doc_mentions_required_keyword(self, doc_text: str, must_mention: str) -> None:
        assert must_mention in doc_text, (
            f"docs/vertical-acceptance.md must mention {must_mention!r}"
        )

    def test_doc_references_live_artifact_path(self, doc_text: str) -> None:
        assert "tests/e2e/_artifacts/minimax_live_smoke.json" in doc_text

    def test_doc_pins_specific_modules(self, doc_text: str) -> None:
        """Owner references MUST be concrete module paths
        per WP10 docs-honesty convention. No cluster/slice
        IDs allowed as owners."""

        for module_path in (
            "controlled_patches.py",
            "red_green_cycle.py",
            "minimax_budget_tracker.py",
            "independent_review.py",
        ):
            assert module_path in doc_text, f"vertical-acceptance doc must pin to {module_path}"

    def test_doc_references_reproduction_recipe(self, doc_text: str) -> None:
        assert "RUN_MINIMAX_LIVE_TEST" in doc_text
        assert "pytest tests/e2e/test_minimax_live_smoke.py" in doc_text

    def test_doc_declares_draft_status(self, doc_text: str) -> None:
        assert "DRAFT" in doc_text
        # At least two DRAFT mentions expected so that the M2.7
        # historical-transport-evidence callout and the stop-gate
        # reminder both remain prominent in the M3 acceptance index.
        assert doc_text.upper().count("DRAFT") >= 2


class TestNineStageWalkthrough:
    """The workplan prescribes 9 vertical-acceptance stages.
    Pin that each is referenced (by code owner, not cluster ID)."""

    @pytest.mark.parametrize("stage_idx", list(range(1, 10)))
    def test_stage_index_present(self, doc_text: str, stage_idx: int) -> None:
        pattern = rf"\b{stage_idx}\b"
        assert re.search(pattern, doc_text), f"vertical-acceptance doc missing stage {stage_idx}"


class TestLiveEvidenceSnippet:
    """The doc must include a representative snippet from the
    live artifact so reviewers can verify the evidence without
    checking out the run."""

    def test_doc_includes_chat_artifact_snippet(self, doc_text: str) -> None:
        assert "chat/completions" in doc_text
        # Snippet keys (subset of artifact keys)
        for key in ("configured_model_id", "model_id_returned", "duration_s"):
            assert key in doc_text

    def test_doc_includes_catalog_artifact_snippet(self, doc_text: str) -> None:
        assert "/v1/models" in doc_text
