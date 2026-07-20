"""Cluster G9 / Tier 4d: GitHub Release assets attachment contract.

Background: When `download-artifact` is invoked with
``merge-multiple: true`` and no ``pattern:`` filter, each artifact
is extracted into a subdirectory named after the artifact. The
subsequent ``softprops/action-gh-release`` ``files:`` globs run
relative to the runner's working directory, so a pattern of
``dist-all/*.whl`` does NOT match ``dist-all/release-artifacts-3.12/dist/*.whl``.

This is a real bug we hit on the v0.2.0 tag cut: the release
shipped with ONLY the SBOM attached (the SBOM artifact happens to
be a flat artifact, not inside a subdirectory). The wheel + sdist
+ Sigstore bundle silently went missing.

The contract: when the release job uses ``merge-multiple: true``
to download build artifacts, the ``files:`` patterns MUST use a
recursive glob (``**/...``) so they descend into per-artifact
subdirectories. The non-recursive pattern ``*.whl`` at the same
level only matches the SBOM (which is uploaded as a flat artifact).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

# Patterns that MUST exist in the github-release job's `files:` block
# (one per asset type we expect to attach).
REQUIRED_PATTERNS = (
    "dist-all/**/*.whl",
    "dist-all/**/*.tar.gz",
    "dist-all/**/*.sigstore.json",
)

# Bare non-recursive patterns we MUST NOT have, because they fail
# to descend into per-artifact subdirs created by
# `download-artifact` with `merge-multiple: true`.
FORBIDDEN_PATTERNS = (
    "dist-all/*.whl",
    "dist-all/*.tar.gz",
)


def _files_block() -> str:
    """Return the literal ``files: |`` block from the github-release
    job, with leading indentation stripped."""
    wf = yaml.safe_load(RELEASE_YML.read_text())
    job = wf["jobs"]["github-release"]
    # Find the step that uses softprops/action-gh-release (the only
    # step that carries a ``files:`` block).
    for step in job["steps"]:
        uses = step.get("uses", "")
        if uses.startswith("softprops/action-gh-release"):
            return step.get("with", {}).get("files", "")
    raise AssertionError(
        "release.yml::github-release has no step using "
        "softprops/action-gh-release; cannot locate the files block."
    )


def test_github_release_files_block_present() -> None:
    """Sanity: the github-release job has a ``files:`` block (else
    the contract below is meaningless).
    """
    files = _files_block()
    assert files and files.strip(), (
        "release.yml::github-release must declare a `files:` block so "
        "the wheel + sdist + SBOM + Sigstore bundles get attached to "
        "the GitHub Release. Without it, the release page ships empty."
    )


@pytest.mark.parametrize("pattern", REQUIRED_PATTERNS)
def test_github_release_attaches_recursive_globs(pattern: str) -> None:
    """Every artifact type MUST be attached via a recursive glob so
    it descends into per-artifact subdirectories created by
    ``download-artifact`` with ``merge-multiple: true``.
    """
    files = _files_block()
    assert pattern in files, (
        f"release.yml::github-release.files must include `{pattern}` "
        f"so the asset is attached even when downloaded into a "
        f"per-artifact subdir (e.g. `dist-all/release-artifacts-3.12/`). "
        f"Got: {files!r}"
    )


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_github_release_does_not_use_bare_globs(pattern: str) -> None:
    """The bare non-recursive form MUST NOT be present. It matches
    only flat artifacts (the SBOM) and silently skips the wheel /
    sdist / Sigstore bundles.
    """
    files = _files_block()
    # We need to match the pattern as a line (with optional leading
    # whitespace); not as a substring of a longer recursive glob.
    lines = {line.strip() for line in files.splitlines()}
    assert pattern not in lines, (
        f"release.yml::github-release.files contains bare glob "
        f"`{pattern}`. This pattern does not descend into "
        f"per-artifact subdirs and will silently skip the wheel / "
        f"sdist / Sigstore bundle. Use `dist-all/**/{pattern.rsplit('/', maxsplit=1)[-1]}` "
        f"instead."
    )


def test_github_release_attaches_sbom() -> None:
    """The SBOM is uploaded as a flat artifact (not inside a
    per-artifact subdir), so a non-recursive pattern is correct.
    """
    files = _files_block()
    assert "dist-all/sbom-cyclonedx.json" in files or any(
        line.endswith("sbom-cyclonedx.json") for line in files.splitlines()
    ), (
        "release.yml::github-release.files must include the SBOM "
        "(`dist-all/sbom-cyclonedx.json`). It's uploaded as a flat "
        "artifact and the release page links it for downstream audit."
    )
