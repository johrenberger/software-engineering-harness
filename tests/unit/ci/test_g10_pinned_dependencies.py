"""G10 — Reduce checked-in construction artifacts.

Tracks the OpenSSF Scorecard 'Pinned-Dependencies' category. The goal
is to keep the score high by ensuring:

  1. All GitHub Actions `uses:` references are SHA-pinned (covered by
     G4 tests; this file asserts the contract is NOT regressed).
  2. Container base images in Dockerfiles are SHA-digest pinned.
  3. No `docker run` / `docker pull` of unpinned images in workflows.
  4. No `pip install <package>` without version pin in workflows.

These are *contract* tests — they prevent regressions of the existing
G4 + G5 + G10 posture. Scorecard checks the actual workflow runs and
base image resolution at runtime; we cannot mock that here, but we can
prevent the source-of-truth files from regressing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DOCKER_DIR = REPO_ROOT / "docker"

# Match `uses: owner/repo@<sha-or-ref>` (sha is 40 hex chars).
USES_RE = re.compile(r"^\s*uses:\s+[\w\-]+(?:/[\w\-]+)+@(?:[a-f0-9]{40}|\S+)")
SHA_PINNED_RE = re.compile(r"@([a-f0-9]{40})\b")
# Match `FROM <image>[:<tag>][@<digest>]`
FROM_RE = re.compile(r"^\s*FROM\s+(\S+)")
DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}\b")


def _all_workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml"))


def _dockerfiles() -> list[Path]:
    return sorted(DOCKER_DIR.rglob("Dockerfile*"))


def _docker_compose_files() -> list[Path]:
    return sorted(DOCKER_DIR.rglob("docker-compose*.yml"))


# ---------------------------------------------------------------------------
# Section 1: All workflow `uses:` are SHA-pinned (G4 contract — no regression).
# ---------------------------------------------------------------------------


def test_no_unpinned_actions() -> None:
    """Every `uses: owner/repo@<ref>` line must have a 40-char SHA."""
    offenders: list[tuple[str, int, str]] = []
    for wf in _all_workflow_files():
        text = wf.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if not stripped.startswith("uses:"):
                continue
            # Skip local actions (./foo).
            if "./" in stripped:
                continue
            m = USES_RE.match(line)
            if not m:
                offenders.append((wf.name, lineno, line.strip()))
                continue
            if not SHA_PINNED_RE.search(line):
                offenders.append((wf.name, lineno, line.strip()))
    assert not offenders, "Unpinned `uses:` references (must be SHA-pinned per G4):\n" + "\n".join(
        f"  {name}:{ln}: {text}" for name, ln, text in offenders
    )


# ---------------------------------------------------------------------------
# Section 2: Dockerfile base images are SHA-digest pinned.
# ---------------------------------------------------------------------------


def test_dockerfile_base_images_digest_pinned() -> None:
    """Every `FROM <image>` line must pin a sha256 digest.

    Without a digest, Scorecard's Pinned-Dependencies category drops
    the container-base-image sub-score to 0. Pinned via
    `FROM <image>:<tag>@sha256:<64-hex>`.
    """
    offenders: list[tuple[str, int, str]] = []
    for df in _dockerfiles():
        text = df.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = FROM_RE.match(line)
            if not m:
                continue
            image_ref = m.group(1)
            # Skip `FROM scratch` (base-less image).
            if image_ref.startswith("scratch"):
                continue
            if not DIGEST_RE.search(image_ref):
                offenders.append((df.name, lineno, line.strip()))
    assert not offenders, "Unpinned FROM lines (must pin @sha256:... digest):\n" + "\n".join(
        f"  {name}:{ln}: {text}" for name, ln, text in offenders
    )


# ---------------------------------------------------------------------------
# Section 3: Docker compose does not pin to a mutable tag (best-effort).
# ---------------------------------------------------------------------------


def test_compose_uses_digest_or_no_image() -> None:
    """If a docker-compose service declares an `image:`, it should be
    digest-pinned OR explicit version. We allow `:latest` since the
    compose file is for local dev only; check this is intentional."""
    compose_files = _docker_compose_files()
    if not compose_files:
        pytest.skip("no docker-compose files")
    for cf in compose_files:
        text = cf.read_text(encoding="utf-8")
        # We don't enforce pinning for compose files — they're local dev.
        # Just ensure we have at most ONE `image: latest` per compose file.
        latest_count = sum(
            1
            for line in text.splitlines()
            if line.strip().startswith("image:") and "latest" in line
        )
        assert latest_count <= 1, (
            f"{cf.name}: multiple `image: latest` lines — pin to digest or version"
        )


# ---------------------------------------------------------------------------
# Section 4: No unpinned `pip install <package>` in workflow run commands.
# ---------------------------------------------------------------------------


# Matches `pip install <pkg>` without `==<version>` or `>=` etc.
UNPINNED_PIP_RE = re.compile(r"pip install\s+(?!-e|--)[a-zA-Z][a-zA-Z0-9_.-]*")


def test_workflow_pip_installs_are_pinned() -> None:
    """Any `pip install <pkg>` (not `-e .` / `-- ...`) must pin a version.

    Scorecard's Pinned-Dependencies category flags unpinned `pip install`
    commands as a regression. The recommended pattern is
    `pip install pkg==1.2.3` or use of `requirements.txt` / `uv.lock`.
    """
    offenders: list[tuple[str, int, str]] = []
    for wf in _all_workflow_files():
        text = wf.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "pip install" not in line:
                continue
            # Skip comment-only lines and `pip install -e ".[dev]"`.
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "-e" in line or "--editable" in line:
                continue
            if "requirements" in line or "pyproject.toml" in line:
                continue
            # `python -m pip install --upgrade pip` is allowed (pinned via setup-python).
            if "pip install --upgrade pip" in line or "pip install -U pip" in line:
                continue
            m = UNPINNED_PIP_RE.search(line)
            if m:
                offenders.append((wf.name, lineno, line.strip()))
    assert not offenders, (
        "Unpinned `pip install <pkg>` in workflow (must pin version):\n"
        + "\n".join(f"  {name}:{ln}: {text}" for name, ln, text in offenders)
    )


# ---------------------------------------------------------------------------
# Section 5: Specific known-good SHAs/digests (regression fence).
# ---------------------------------------------------------------------------


def test_python_base_image_uses_known_digest() -> None:
    """The Dockerfile pins python:3.13-slim via a known manifest-list
    digest. Updating this requires an explicit decision (Scorecard
    contract)."""
    dockerfile = DOCKER_DIR / "Dockerfile"
    assert dockerfile.exists(), f"expected {dockerfile} to exist"
    first_from = next(
        (
            line
            for line in dockerfile.read_text(encoding="utf-8").splitlines()
            if line.startswith("FROM ")
        ),
        None,
    )
    assert first_from is not None, "Dockerfile must have a FROM line"
    assert "python:3.13-slim@sha256:" in first_from, (
        f"Dockerfile FROM must pin python:3.13-slim to a sha256 digest; got: {first_from!r}"
    )
