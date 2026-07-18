"""Tests that the configuration loader rejects unknown model providers.

Slice 1, Behavior 3: model routing validation. Every routing slot and
fallback pair must name a registered provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from seharness.config_loader import ConfigurationError, load_config


class TestRoutingSlotValidation:
    def test_unknown_planning_provider_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "harness.yaml"
        cfg.write_text(yaml.safe_dump({"models": {"planning": "gpt-99-ultra"}}))
        with pytest.raises(ConfigurationError, match="planning"):
            load_config(repo_yaml=cfg)

    def test_unknown_implementation_provider_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "harness.yaml"
        cfg.write_text(yaml.safe_dump({"models": {"implementation": "watson-9000"}}))
        with pytest.raises(ConfigurationError, match="implementation"):
            load_config(repo_yaml=cfg)

    def test_known_providers_accepted(self, tmp_path: Path) -> None:
        cfg = tmp_path / "harness.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "models": {
                        "planning": "minimax",
                        "implementation": "codex",
                        "remediation": "minimax",
                        "review": "codex",
                    }
                }
            )
        )
        config = load_config(repo_yaml=cfg)
        assert config.models.planning == "minimax"
        assert config.models.implementation == "codex"

    def test_fallback_must_reference_known_provider(self, tmp_path: Path) -> None:
        cfg = tmp_path / "harness.yaml"
        cfg.write_text(yaml.safe_dump({"models": {"fallback": {"minimax": "watson"}}}))
        with pytest.raises(ConfigurationError):
            load_config(repo_yaml=cfg)


class TestRoutingRegression:
    def test_default_routing_is_self_consistent(self, tmp_path: Path) -> None:
        config = load_config(repo_yaml=tmp_path / "missing.yaml")
        # All default routing slots must reference registered providers.
        for slot in (
            config.models.planning,
            config.models.implementation,
            config.models.remediation,
            config.models.review,
        ):
            assert slot in ("minimax", "codex")
        # Every fallback target must also be a registered provider.
        for target in config.models.fallback.values():
            assert target in ("minimax", "codex")
