"""Contract tests for G7 — SBOM + build provenance attestation.

G7 ships two supply-chain artifacts in every CI run on main:
  1. SBOM (CycloneDX JSON) produced by anchore/sbom-action@v0, uploaded
     as the `sbom` artifact (30-day retention).
  2. Build provenance (SLSA L1) produced by actions/attest-build-provenance@v1.

This file pins the structural rules of `.github/workflows/ci.yml` plus
the security-relevant configuration so accidental config changes get
caught in CI.

References:
- G7 spec: docs/analysis/2026-07-19-priority-stories.md
- anchore/sbom-action@v0: https://github.com/anchore/sbom-action
- actions/attest-build-provenance@v1: https://github.com/actions/attest
- CycloneDX spec: https://cyclonedx.org/specification/overview/
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASING_DOC = REPO_ROOT / "docs" / "releasing.md"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text())


@pytest.fixture(scope="module")
def ci_text() -> str:
    return CI_WORKFLOW.read_text()


def _step_by_name(workflow: dict, name: str) -> dict:
    for s in workflow.get("jobs", {}).get("quality-gate", {}).get("steps", []):
        if s.get("name") == name:
            return s
    return {}


# ----------------------------------------------------------------------
# 1. Permissions block: attestations + id-token must be granted
# ----------------------------------------------------------------------


def test_ci_workflow_has_permissions_block(ci_workflow: dict) -> None:
    """G7 requires a top-level permissions block (G7 + G4 supply-chain posture).

    Without explicit permissions, GitHub grants the GITHUB_TOKEN the
    workflow's default scopes (read for contents, write for everything
    else). The minimum-privilege posture means we MUST declare what we
    need.
    """
    assert "permissions" in ci_workflow, (
        "ci.yml must declare a top-level `permissions:` block "
        "(G4 + G7 supply-chain posture: minimum privilege)"
    )


def test_permissions_include_attestations_write(ci_workflow: dict) -> None:
    """attestations: write is required by actions/attest-build-provenance@v1."""
    perms = ci_workflow.get("permissions", {})
    assert perms.get("attestations") == "write", (
        f"ci.yml permissions must grant `attestations: write` "
        f"(needed by actions/attest-build-provenance). Got: {perms}"
    )


def test_permissions_include_id_token_write(ci_workflow: dict) -> None:
    """id-token: write is needed for OIDC token exchange in attestation."""
    perms = ci_workflow.get("permissions", {})
    assert perms.get("id-token") == "write", (
        f"ci.yml permissions must grant `id-token: write` "
        f"(OIDC for attestation signing). Got: {perms}"
    )


def test_permissions_contents_is_read_only(ci_workflow: dict) -> None:
    """contents MUST be `read` (not `write`) — defense in depth (G4)."""
    perms = ci_workflow.get("permissions", {})
    assert perms.get("contents") == "read", (
        f"ci.yml permissions must declare `contents: read` (minimum privilege). Got: {perms}"
    )


# ----------------------------------------------------------------------
# 2. Build SBOM step (anchore/sbom-action@v0)
# ----------------------------------------------------------------------


def test_ci_workflow_has_build_sbom_step(ci_workflow: dict) -> None:
    """ci.yml must include a SBOM-build step."""
    step = _step_by_name(ci_workflow, "Build SBOM (CycloneDX)")
    assert step, "ci.yml must include a step named 'Build SBOM (CycloneDX)'"
    assert step.get("uses"), "Build SBOM step must declare a `uses:` action"


def test_build_sbom_uses_anchore_sbom_action(ci_workflow: dict) -> None:
    """Build SBOM must use anchore/sbom-action@v0 (mature, syft-based)."""
    step = _step_by_name(ci_workflow, "Build SBOM (CycloneDX)")
    uses = step.get("uses", "")
    assert "anchore/sbom-action" in uses, (
        f"Build SBOM must use anchore/sbom-action. Got uses={uses!r}"
    )


def test_build_sbom_output_format_is_cyclonedx(ci_workflow: dict) -> None:
    """Build SBOM must produce CycloneDX JSON (standard format)."""
    step = _step_by_name(ci_workflow, "Build SBOM (CycloneDX)")
    with_block = step.get("with", {})
    fmt = with_block.get("format", "")
    assert "cyclonedx" in fmt.lower(), (
        f"Build SBOM.format must include `cyclonedx`. Got format={fmt!r}"
    )


def test_build_sbom_runs_only_on_push_to_main(ci_workflow: dict) -> None:
    """Build SBOM runs only on push to main (avoid noisy PR runs).

    SBOM generation takes ~10s; on PRs the SBOM is transient and
    never gets audited (consumers read it from `main`).
    """
    step = _step_by_name(ci_workflow, "Build SBOM (CycloneDX)")
    condition = step.get("if", "")
    assert "push" in condition and "main" in condition, (
        f"Build SBOM must be gated on push to main only (got if={condition!r})"
    )


def test_sbom_stage_step_exists(ci_workflow: dict, ci_text: str) -> None:
    """A 'Stage SBOM for attestation' step must exist.

    anchore/sbom-action@v0 writes the SBOM to /tmp/sbom-action-XXX/, not
    to the workspace root. We need to download the artifact it uploaded
    to a known workspace path so the attest step can find it.

    Caught in CI run 29703892749: the original 'Upload SBOM artifact'
    step pointed at the workspace path that anchore's action never wrote
    to, making the downstream attest step fail with "Could not find
    subject at path sbom-cyclonedx.json".
    """
    step = _step_by_name(ci_workflow, "Stage SBOM for attestation")
    assert step, "ci.yml must include a 'Stage SBOM for attestation' step"
    # G4: action is SHA-pinned. Check the action name from the parsed YAML;
    # check the version comment via the raw text (PyYAML strips comments).
    uses = step.get("uses", "")
    assert uses.startswith("actions/download-artifact@"), (
        f"Stage SBOM step must use actions/download-artifact. Got uses={uses!r}"
    )
    assert "actions/download-artifact@" in ci_text and re.search(
        r"actions/download-artifact@[\da-f]+ # v4", ci_text
    ), (
        "ci.yml must have `actions/download-artifact@<sha> # v4` (G4 pinned "
        "with version comment for readability)."
    )
    with_block = step.get("with", {})
    assert with_block.get("name") == "sbom-cyclonedx.json", (
        f"Stage SBOM step must download the artifact named "
        f"'sbom-cyclonedx.json' (matches what anchore/sbom-action uploads). "
        f"Got name={with_block.get('name')!r}"
    )


# ----------------------------------------------------------------------
# 3. Attest build provenance step
# ----------------------------------------------------------------------


def test_ci_workflow_has_attest_step(ci_workflow: dict) -> None:
    """ci.yml must include a SLSA provenance attestation step."""
    step = _step_by_name(ci_workflow, "Attest build provenance")
    assert step, "ci.yml must include an 'Attest build provenance' step"
    assert step.get("uses"), "Attest build provenance step must declare a `uses:`"


def test_attest_uses_official_github_action(ci_workflow: dict, ci_text: str) -> None:
    """Attestation must use actions/attest-build-provenance (GitHub-official)."""
    step = _step_by_name(ci_workflow, "Attest build provenance")
    uses = step.get("uses", "")
    assert "actions/attest-build-provenance" in uses, (
        f"Attest step must use actions/attest-build-provenance. Got uses={uses!r}"
    )
    # G4: SHA-pinned. PyYAML strips trailing comments, so we read the
    # raw YAML text and check the comment is present.
    m = re.search(
        r"actions/attest-build-provenance@[\da-f]+ # v(\d+)",
        ci_text,
    )
    assert m and int(m.group(1)) >= 1, (
        "attest-build-provenance must have `# v1+` comment after SHA. Looked in ci.yml."
    )


def test_attest_subject_references_an_artifact_path(ci_workflow: dict) -> None:
    """Attest action must point at a file to sign (subject-path).

    An attestation with no subject is a no-op — actions/attest-build-provenance
    requires either subject-path (a glob) or subject-digest. Without one,
    the action errors with `One of subject-path or subject-digest must be
    provided` (caught in CI run 29703712905 after the PR #29 merge).
    """
    step = _step_by_name(ci_workflow, "Attest build provenance")
    with_block = step.get("with", {})
    # Must have one of subject-path or subject-digest.
    has_path = bool(with_block.get("subject-path"))
    has_digest = bool(with_block.get("subject-digest"))
    assert has_path or has_digest, (
        f"Attest build provenance must declare `subject-path` (or `subject-digest`); "
        f"an attestation without a subject is invalid. Got with={with_block!r}"
    )
    if has_path:
        # The path must point at a file that the workflow produces (or could produce).
        # We just require it to be a non-empty string with a `.<ext>` suffix.
        path = with_block["subject-path"]
        assert isinstance(path, str) and "." in path, (
            f"subject-path must be a non-empty string with a file extension (got {path!r})"
        )


def test_attest_runs_only_on_push_to_main(ci_workflow: dict) -> None:
    """Attestation runs only on push to main (otherwise clutters PRs)."""
    step = _step_by_name(ci_workflow, "Attest build provenance")
    condition = step.get("if", "")
    assert "push" in condition and "main" in condition, (
        f"Attest must be gated on push to main only (got if={condition!r})"
    )


# ----------------------------------------------------------------------
# 4. Existing steps still in correct order (regression check)
# ----------------------------------------------------------------------


def test_sbom_and_provenance_come_after_test_artifacts(
    ci_workflow: dict,
) -> None:
    """SBOM + attest must come AFTER test-artifacts (so they cover the run)."""
    steps = ci_workflow["jobs"]["quality-gate"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert names.index("upload-test-artifacts") < names.index("Build SBOM (CycloneDX)"), (
        "Build SBOM must come after upload-test-artifacts"
    )
    assert names.index("Build SBOM (CycloneDX)") < names.index("Attest build provenance"), (
        "Attest build provenance must come after Build SBOM"
    )
    assert names.index("Attest build provenance") < names.index("render-dashboard"), (
        "Attest must come before render-dashboard (dashboard is just summarization)"
    )


# ----------------------------------------------------------------------
# 5. Release docs (docs/releasing.md) — preparatory for G18
# ----------------------------------------------------------------------


def test_releasing_md_exists() -> None:
    """docs/releasing.md should exist (preparatory for G18 release automation)."""
    assert RELEASING_DOC.is_file(), (
        "G7 follow-up requires docs/releasing.md to document the "
        "release process (SBOM + provenance + signing)"
    )


def test_releasing_md_describes_sbom() -> None:
    """releasing.md must mention SBOM in the release process."""
    text = RELEASING_DOC.read_text()
    assert re.search(r"\bSBOM\b", text), (
        "releasing.md must mention SBOM as part of the release process"
    )


def test_releasing_md_describes_provenance_or_attestation() -> None:
    """releasing.md must mention provenance attestation."""
    text = RELEASING_DOC.read_text()
    assert re.search(r"\b(provenance|attestation)\b", text, re.IGNORECASE), (
        "releasing.md must mention provenance or attestation"
    )


def test_releasing_md_step_by_step() -> None:
    """releasing.md should be a step-by-step runbook (numbered list or shell blocks)."""
    text = RELEASING_DOC.read_text()
    # Look for either a numbered list or shell command blocks.
    has_numbered = bool(re.search(r"^\s*\d+\.\s+", text, re.MULTILINE))
    has_bash_blocks = text.count("```bash") >= 1
    assert has_numbered or has_bash_blocks, (
        "releasing.md must be a step-by-step runbook (numbered list or bash blocks)"
    )
