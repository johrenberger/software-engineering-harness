"""Tests for configuration precedence resolution.

Slice 1, Behavior 2: precedence resolution.
Order (highest priority first):
    1. CLI overrides
    2. Environment variables
    3. Local config file (seharness.local.yaml)
    4. Repository harness.yaml
    5. Built-in defaults
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from seharness.config_loader import (
    ConfigurationError,
    load_config,
)


class TestPrecedenceOrder:
    """Higher-priority sources override lower-priority sources."""

    def test_repo_yaml_overrides_defaults(self, tmp_path: Path) -> None:
        repo_cfg = tmp_path / "harness.yaml"
        repo_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-repo"}}))
        config = load_config(repo_yaml=repo_cfg)
        assert config.harness.artifact_root == "/tmp/from-repo"

    def test_local_yaml_overrides_repo_yaml(self, tmp_path: Path) -> None:
        repo_cfg = tmp_path / "harness.yaml"
        repo_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-repo"}}))
        local_cfg = tmp_path / "seharness.local.yaml"
        local_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-local"}}))
        config = load_config(
            repo_yaml=repo_cfg,
            local_yaml=local_cfg,
        )
        assert config.harness.artifact_root == "/tmp/from-local"

    def test_env_overrides_local_yaml(self, tmp_path: Path, monkeypatch) -> None:
        repo_cfg = tmp_path / "harness.yaml"
        repo_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-repo"}}))
        local_cfg = tmp_path / "seharness.local.yaml"
        local_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-local"}}))
        # SEHARNESS_HARNESS__ARTIFACT_ROOT uses dunder for nested keys
        monkeypatch.setenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", "/tmp/from-env")
        config = load_config(
            repo_yaml=repo_cfg,
            local_yaml=local_cfg,
        )
        assert config.harness.artifact_root == "/tmp/from-env"

    def test_cli_overrides_env(self, tmp_path: Path, monkeypatch) -> None:
        repo_cfg = tmp_path / "harness.yaml"
        repo_cfg.write_text(yaml.safe_dump({"harness": {"artifact_root": "/tmp/from-repo"}}))
        monkeypatch.setenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", "/tmp/from-env")
        cli_overrides = {"harness": {"artifact_root": "/tmp/from-cli"}}
        config = load_config(
            repo_yaml=repo_cfg,
            cli_overrides=cli_overrides,
        )
        assert config.harness.artifact_root == "/tmp/from-cli"


class TestDefaultsAreUsedWhenNothingProvided:
    def test_no_sources_returns_defaults(self, tmp_path: Path) -> None:
        # No repo yaml, no local yaml, no env, no CLI. Use a tmp_path that
        # explicitly does not contain a harness.yaml.
        config = load_config(repo_yaml=tmp_path / "no-such.yaml")
        assert config.harness.artifact_root == ".harness-runs"
        assert config.repository.base_branch == "main"
        assert config.github.auto_merge is False


class TestUnknownKeyStillRejectedAfterLoading:
    def test_unknown_key_in_yaml_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "harness.yaml"
        bad.write_text(yaml.safe_dump({"unknown_key": "value"}))
        with pytest.raises(ConfigurationError):
            load_config(repo_yaml=bad)

    def test_unknown_key_in_env_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("SEHARNESS_UNKNOWN_KEY", "x")
        with pytest.raises(ConfigurationError):
            load_config()
