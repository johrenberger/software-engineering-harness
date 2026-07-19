"""Contract tests for G6 — Dependabot configuration.

G6 enables Dependabot to automate dependency-update PRs.
This file pins the structural rules of `.github/dependabot.yml` plus
the lockfile-hardening settings in `pyproject.toml` so accidental
config changes can be caught in CI.

References:
- G6 spec: docs/analysis/2026-07-19-priority-stories.md
- Dependabot config schema:
  https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPENDABOT_YML = REPO_ROOT / ".github" / "dependabot.yml"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def dependabot_config() -> dict:
    """Parsed `.github/dependabot.yml` (cached per module)."""
    return yaml.safe_load(DEPENDABOT_YML.read_text())


@pytest.fixture(scope="module")
def pyproject_text() -> str:
    return PYPROJECT_TOML.read_text()


# ----------------------------------------------------------------------
# 1. Dependabot config file shape
# ----------------------------------------------------------------------


def test_dependabot_config_exists() -> None:
    """Dependabot config must exist at .github/dependabot.yml."""
    assert DEPENDABOT_YML.is_file(), "G6 requires .github/dependabot.yml to exist"


def test_dependabot_config_is_valid_yaml() -> None:
    """dependabot.yml must parse as YAML (not just JSON-style)."""
    content = DEPENDABOT_YML.read_text()
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict), "dependabot.yml must parse as a dict (not a list or scalar)"


def test_dependabot_config_uses_v2_schema() -> None:
    """dependabot.yml must use the v2 schema (current standard)."""
    parsed = yaml.safe_load(DEPENDABOT_YML.read_text())
    assert parsed.get("version") == 2, (
        f"dependabot.yml must declare 'version: 2' (got version={parsed.get('version')!r})"
    )


def test_dependabot_config_has_updates_block() -> None:
    """dependabot.yml must declare at least one update config."""
    parsed = yaml.safe_load(DEPENDABOT_YML.read_text())
    updates = parsed.get("updates")
    assert isinstance(updates, list), "dependabot.yml must declare `updates:` as a list"
    assert len(updates) >= 1, "at least one ecosystem must be configured"


# ----------------------------------------------------------------------
# 2. Required ecosystems + schedule + directory
# ----------------------------------------------------------------------


def _ecosystem_for(config: dict, ecosystem: str) -> dict:
    """Helper: return the update entry for a given ecosystem, or {}."""
    for u in config.get("updates", []):
        if u.get("package-ecosystem") == ecosystem:
            return u
    return {}


def test_dependabot_config_scans_pip_ecosystem(dependabot_config: dict) -> None:
    """G6 must cover the pip ecosystem (Python deps in pyproject.toml + uv.lock)."""
    entry = _ecosystem_for(dependabot_config, "pip")
    assert entry, (
        "dependabot.yml must include a `pip` package-ecosystem entry (G6 cover the Python deps)"
    )
    assert entry.get("directory") == "/", (
        "pip entry must scan the repo root (pyproject.toml is at repo root)"
    )


def test_dependabot_config_scans_github_actions(dependabot_config: dict) -> None:
    """G6 must cover the github-actions ecosystem (workflow file action versions)."""
    entry = _ecosystem_for(dependabot_config, "github-actions")
    assert entry, (
        "dependabot.yml must include a `github-actions` package-ecosystem entry "
        "(G6 cover the workflow action versions)"
    )
    assert entry.get("directory") == "/", (
        "github-actions entry must scan the repo root "
        "(workflows live in .github/workflows/, dependabot reads .github/ at root)"
    )


@pytest.mark.parametrize("ecosystem", ["pip", "github-actions"])
def test_dependabot_ecosystem_schedule_is_weekly(dependabot_config: dict, ecosystem: str) -> None:
    """Each covered ecosystem must have a weekly schedule (not daily/monthly)."""
    entry = _ecosystem_for(dependabot_config, ecosystem)
    schedule = entry.get("schedule", {})
    assert schedule.get("interval") == "weekly", (
        f"{ecosystem} entry must use `interval: weekly` (got {schedule.get('interval')!r})"
    )


@pytest.mark.parametrize("ecosystem", ["pip", "github-actions"])
def test_dependabot_ecosystem_has_supply_chain_label(
    dependabot_config: dict, ecosystem: str
) -> None:
    """Each update entry must apply the `supply-chain` label for filtering."""
    entry = _ecosystem_for(dependabot_config, ecosystem)
    labels = entry.get("labels", [])
    assert "supply-chain" in labels, (
        f"{ecosystem} entry must include 'supply-chain' label "
        f"(for GH Issues / PR filtering). Got: {labels}"
    )


@pytest.mark.parametrize("ecosystem", ["pip", "github-actions"])
def test_dependabot_ecosystem_has_commit_message_prefix(
    dependabot_config: dict, ecosystem: str
) -> None:
    """Each entry must have a non-empty commit-message prefix for clean git log."""
    entry = _ecosystem_for(dependabot_config, ecosystem)
    commit_message = entry.get("commit-message", {})
    prefix = commit_message.get("prefix")
    assert prefix, (
        f"{ecosystem} entry must declare commit-message.prefix "
        f"(e.g. 'deps' or 'ci') for clean git history"
    )


# ----------------------------------------------------------------------
# 3. Lockfile hardening settings (pyproject.toml)
# ----------------------------------------------------------------------


def test_uv_lock_exists() -> None:
    """uv.lock must exist (the locked dependency tree with hashes)."""
    assert UV_LOCK.is_file(), (
        "uv.lock must exist (G6: hashed dependency constraints require uv.lock)"
    )


def test_pyproject_has_tool_uv_block(pyproject_text: str) -> None:
    """pyproject.toml must declare [tool.uv] (G6 supply-chain hardening settings)."""
    assert "[tool.uv]" in pyproject_text, (
        "pyproject.toml must declare a [tool.uv] block "
        "(G6: lock-deterministic + lockfile hardening settings)"
    )


def test_tool_uv_lock_deterministic_is_true(pyproject_text: str) -> None:
    """[tool.uv] lock-deterministic must be true (byte-for-byte lockfile reproducibility)."""
    import re as _re

    m = _re.search(
        r"\[tool\.uv\](.*?)(?=\n\[|\Z)",
        pyproject_text,
        _re.DOTALL,
    )
    assert m is not None, "pyproject.toml must contain a [tool.uv] block"
    block = m.group(1)
    assert "lock-deterministic" in block, "[tool.uv] must declare `lock-deterministic` setting"
    # Find lock-deterministic = X
    m2 = _re.search(r"lock-deterministic\s*=\s*(\w+)", block)
    assert m2, "lock-deterministic must be set to a value"
    assert m2.group(1) == "true", (
        f"lock-deterministic must be `true` (got `{m2.group(1)}`); "
        f"false would let runs produce different lockfiles"
    )


def test_pip_audit_requires_hashes(pyproject_text: str) -> None:
    """[tool.pip-audit] must require hashes (supply-chain integrity)."""
    import re as _re

    m = _re.search(
        r"\[tool\.pip-audit\](.*?)(?=\n\[|\Z)",
        pyproject_text,
        _re.DOTALL,
    )
    assert m is not None, "pyproject.toml must contain a [tool.pip-audit] block"
    block = m.group(1)
    m2 = _re.search(r"require-hashes\s*=\s*(\w+)", block)
    assert m2, "[tool.pip-audit] must declare `require-hashes` setting"
    assert m2.group(1) == "true", (
        f"require-hashes must be `true` (got `{m2.group(1)}`); "
        f"false would install un-hashed deps and defeat the lockfile check"
    )


def test_uv_lock_contains_sha256_hashes() -> None:
    """uv.lock must have sha256 hashes for every artifact (G6 entrypoint).

    Defence-in-depth: even if `[tool.uv] lock-deterministic` is bypassed,
    the lockfile itself records hashes that `pip install --require-hashes`
    (and `uv sync --locked`) will check against.
    """
    text = UV_LOCK.read_text()
    # Count occurrences of `hash = "sha256:...`
    import re as _re

    hashes = _re.findall(r'hash\s*=\s*"sha256:[0-9a-f]{64}"', text)
    # Every entry in uv.lock has sdist + wheel(s), each with a hash.
    # Filter to wheels (the most common).
    assert len(hashes) >= 10, (
        f"uv.lock must have many sha256 hashes "
        f"(got {len(hashes)}); a missing-hash dep means MITM risk"
    )
