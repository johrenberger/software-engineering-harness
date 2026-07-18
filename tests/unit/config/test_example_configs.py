"""Tests that committed example configurations are valid.

These are small regression tests so future schema changes that would
break the examples fail the suite loudly instead of silently producing
a broken scaffold.
"""

from __future__ import annotations

from pathlib import Path

from seharness.config_loader import load_config

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_committed_harness_yaml_validates() -> None:
    config = load_config(repo_yaml=REPO_ROOT / "harness.yaml")
    assert config.repository.base_branch == "main"
    assert config.github.auto_merge is False
    assert config.telegram.enabled is False


def test_local_yaml_example_validates() -> None:
    config = load_config(repo_yaml=REPO_ROOT / "examples" / "harness.local.yaml")
    # local.yaml is mostly commented out, so defaults should hold.
    assert config.harness.artifact_root == ".harness-runs"


def test_default_config_has_no_dangerous_settings() -> None:
    """The scaffold must default to safe settings so misconfiguration is hard."""
    config = load_config(repo_yaml=REPO_ROOT / "harness.yaml")
    assert config.github.auto_merge is False  # never auto-merge in v1
    assert config.harness.fail_closed is True
