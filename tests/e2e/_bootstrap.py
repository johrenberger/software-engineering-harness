"""Cluster M3-4: e2e test bootstrap helpers.

Shared by ``tests/e2e/test_m3_offline_vertical.py`` and any future
M3-4-derived acceptance tests. Exposes:

- ``bootstrap_health_fixture_repo(tmp_path) -> Path``: copy the
  ``tests/fixtures/health_fixture_repo/`` directory to ``tmp_path``,
  ``git init`` it, and make an initial commit so the orchestrator's
  ``_git_head`` capture (M3-3) gets a real SHA.
- ``load_recording_manifest() -> dict``: load the manifest at
  ``tests/fixtures/minimax_m3_recordings/manifest.json`` and assert
  its schema. The loader prefers the live manifest at
  ``tests/fixtures/minimax_m3_recordings_live/manifest.json`` when
  present (M3-5 swap path).
- ``load_recording_pair(phase: str) -> tuple[MiniMaxRequest, MiniMaxTransportResponse]``:
  load the request + response JSON for a given phase from the
  active manifest, validate each through its Pydantic model, and
  return both. Errors fail loudly.
- ``redaction_clean(content_text: str) -> bool``: assert the body
  carries no leaked credentials (sk-, Authorization, JWT-shaped,
  hex blobs ≥ 32 chars).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404 — bootstrap only, args hard-coded
from pathlib import Path
from typing import Any

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
_HEALTH_FIXTURE_REPO = _FIXTURE_DIR / "health_fixture_repo"
_SYNTHETIC_RECORDINGS_DIR = _FIXTURE_DIR / "minimax_m3_recordings"
_LIVE_RECORDINGS_DIR = _FIXTURE_DIR / "minimax_m3_recordings_live"


def active_recordings_dir() -> Path:
    """Return the recordings directory the offline test should use.

    Cluster M3-4 ships with synthetic recordings under
    ``minimax_m3_recordings/``. M3-5 will add a sibling
    ``minimax_m3_recordings_live/`` directory; when that exists
    the live recordings take precedence so the same test code
    drives M3-4 (offline) and M3-5 (live) without conditional
    branching.
    """
    if _LIVE_RECORDINGS_DIR.exists():
        return _LIVE_RECORDINGS_DIR
    return _SYNTHETIC_RECORDINGS_DIR


def bootstrap_health_fixture_repo(tmp_path: Path) -> Path:
    """Copy the health fixture to ``tmp_path`` and ``git init`` it.

    The copy is destructive on existing contents at
    ``tmp_path/health_fixture_repo``. The repository is initialized
    with a ``main`` branch and an initial commit so the
    orchestrator's ``_git_head`` capture (cluster M3-3) returns a
    real SHA. Returns the absolute path of the freshly-created
    repo.
    """
    if not _HEALTH_FIXTURE_REPO.exists():
        msg = (
            f"health fixture repo missing at {_HEALTH_FIXTURE_REPO!s}; "
            "the M3-4 fixtures should be committed alongside this test"
        )
        raise FileNotFoundError(msg)
    target = tmp_path / "health_fixture_repo"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(_HEALTH_FIXTURE_REPO, target)
    # git init + initial commit so ``_git_head`` captures a real SHA.
    # Bandit flags subprocess without an absolute path; we resolve
    # ``git`` via shutil.which and pass only hard-coded args.
    import shutil as _shutil

    git_path = _shutil.which("git")
    if git_path is None:
        msg = "git executable not found on PATH; cannot bootstrap fixture"
        raise RuntimeError(msg)
    env = {
        "GIT_AUTHOR_NAME": "m3-fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "m3-fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "PATH": _shutil.which("git") and "/usr/bin:/usr/local/bin:/bin",
    }
    for cmd in (
        [git_path, "init", "--initial-branch=main"],
        [git_path, "config", "user.email", "fixture@example.invalid"],
        [git_path, "config", "user.name", "m3-fixture"],
        [git_path, "add", "."],
        [git_path, "commit", "-m", "initial-fixture"],
    ):
        result = subprocess.run(  # nosec B603 — args are hard-coded
            cmd,
            cwd=target,
            capture_output=True,
            check=False,
            env=env,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            msg = (
                f"git command {cmd!r} failed in {target}: "
                f"rc={result.returncode} stderr={result.stderr!r}"
            )
            raise RuntimeError(msg)
    return target


def load_recording_manifest() -> dict[str, Any]:
    """Load the active manifest and assert its required schema."""
    manifest_path = active_recordings_dir() / "manifest.json"
    if not manifest_path.exists():
        msg = (
            f"recordings manifest missing at {manifest_path!s}; "
            "M3-4 (offline) requires "
            "tests/fixtures/minimax_m3_recordings/manifest.json"
        )
        raise FileNotFoundError(msg)
    data = json.loads(manifest_path.read_text())
    required = {"schema_version", "model", "recording_kind", "calls"}
    missing = required - set(data)
    if missing:
        msg = f"manifest missing required keys: {sorted(missing)}"
        raise ValueError(msg)
    if data["model"] != "MiniMax-M3":
        msg = f"manifest model must be 'MiniMax-M3', got {data['model']!r}"
        raise ValueError(msg)
    if data["recording_kind"] not in {
        "synthetic_redacted_placeholder",
        "live_recording",
    }:
        msg = (
            f"manifest recording_kind must be "
            f"synthetic_redacted_placeholder or live_recording, "
            f"got {data['recording_kind']!r}"
        )
        raise ValueError(msg)
    if not isinstance(data["calls"], list) or not data["calls"]:
        msg = "manifest 'calls' must be a non-empty list"
        raise ValueError(msg)
    for entry in data["calls"]:
        for key in ("phase", "request_file", "response_file"):
            if key not in entry:
                msg = f"manifest call entry missing key {key!r}: {entry!r}"
                raise ValueError(msg)
    return data


def load_recording_pair(
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the (request, response) JSON pair for ``phase``.

    Returns raw dicts; the caller validates against Pydantic models.
    Errors fail loudly with the active manifest path so the test
    output is actionable.
    """
    manifest = load_recording_manifest()
    target: dict[str, Any] | None = None
    for entry in manifest["calls"]:
        if entry["phase"] == phase:
            target = entry
            break
    if target is None:
        msg = (
            f"phase {phase!r} not in manifest; "
            f"available phases: "
            f"{[e['phase'] for e in manifest['calls']]!r}"
        )
        raise ValueError(msg)
    base = active_recordings_dir()
    request_path = base / target["request_file"]
    response_path = base / target["response_file"]
    if not request_path.exists():
        msg = f"recording request file missing: {request_path!s}"
        raise FileNotFoundError(msg)
    if not response_path.exists():
        msg = f"recording response file missing: {response_path!s}"
        raise FileNotFoundError(msg)
    request_data = json.loads(request_path.read_text())
    response_data = json.loads(response_path.read_text())
    return request_data, response_data


def redaction_clean(text: str) -> bool:
    """Return True when ``text`` carries no leaked credentials.

    Cluster M3-4: every recording body must pass this check. The
    redaction rules mirror the M3-2 ``redact_error_message``:

    - No ``sk-`` followed by 8+ alphanumeric chars (the canonical
      MiniMax API key prefix + body length; the bare ``sk-`` in
      identifiers like ``task-001`` is intentionally allowed).
    - No ``Authorization:`` header (any case).
    - No JWT-shaped tokens (two ``eyJ``-prefixed segments).
    - No hex blobs ≥ 32 chars (likely hashes or keys).
    """
    if not text:
        return True
    if re.search(r"sk-[A-Za-z0-9]{8,}", text):
        return False
    if re.search(r"(?i)authorization\s*:", text):
        return False
    if re.search(r"eyJ[A-Za-z0-9_\-]{20,}\.eyJ[A-Za-z0-9_\-]{20,}", text):
        return False
    return not re.search(r"\b[0-9a-f]{32,}\b", text)


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of ``path``'s contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = [
    "active_recordings_dir",
    "bootstrap_health_fixture_repo",
    "file_sha256",
    "load_recording_manifest",
    "load_recording_pair",
    "redaction_clean",
]
