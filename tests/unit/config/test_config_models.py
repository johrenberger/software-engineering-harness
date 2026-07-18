"""Tests for typed configuration models.

Slice 1, Behavior 1: typed configuration models with strict unknown-key rejection.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.config import (
    ExecutionConfig,
    GitHubConfig,
    HarnessConfig,
    ModelRouting,
    ModelsConfig,
    RepositoryConfig,
    TelegramConfig,
)


class TestHarnessConfigStrictUnknownKeyRejection:
    """The harness MUST reject unknown configuration keys."""

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            HarnessConfig.model_validate({"unknown_key": "value"})
        # The error must mention the offending key
        assert "unknown_key" in str(exc_info.value)

    def test_unknown_nested_key_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            HarnessConfig.model_validate(
                {
                    "harness": {"artifact_root": ".harness-runs", "evil_key": True},
                }
            )
        assert "evil_key" in str(exc_info.value)

    def test_known_keys_accepted(self) -> None:
        config = HarnessConfig.model_validate({})
        assert config.harness.artifact_root == ".harness-runs"
        assert config.harness.resume_enabled is True
        assert config.harness.fail_closed is True


class TestModelsConfig:
    """ModelsConfig must declare routing slots and enforce unknown-model checks."""

    def test_models_config_has_required_routing_slots(self) -> None:
        models = ModelsConfig()
        assert models.planning == "minimax"
        assert models.implementation == "codex"
        assert models.remediation == "codex"
        assert models.review == "minimax"
        assert models.fallback.get("minimax") == "codex"
        assert models.fallback.get("codex") == "minimax"

    def test_unknown_model_id_rejected_at_models_layer(self) -> None:
        with pytest.raises(ValidationError):
            ModelsConfig(planning="not-a-real-model")


class TestRepositoryConfig:
    def test_repository_defaults(self) -> None:
        repo = RepositoryConfig()
        assert repo.clone_root == ".workspaces"
        assert repo.base_branch == "main"
        assert repo.branch_prefix == "ai/feature"


class TestExecutionConfig:
    def test_execution_defaults(self) -> None:
        exe = ExecutionConfig()
        assert exe.max_parallel_tasks == 1
        assert exe.task_retry_limit == 2
        assert exe.validation_repair_limit == 3
        assert exe.review_repair_limit == 2
        assert exe.ci_repair_limit == 2


class TestGitHubConfig:
    def test_github_defaults_safe(self) -> None:
        gh = GitHubConfig()
        assert gh.create_pull_request is True
        assert gh.draft_pull_request is True
        assert gh.mark_ready_when_green is True
        # Auto-merge must be off by default for the scaffold.
        assert gh.auto_merge is False


class TestTelegramConfig:
    def test_telegram_disabled_by_default(self) -> None:
        tg = TelegramConfig()
        assert tg.enabled is False
        assert tg.allowed_chat_ids == []


class TestModelRouting:
    """ModelRouting declares the registry of available providers."""

    def test_routing_registry_minimal(self) -> None:
        routing = ModelRouting()
        assert "minimax" in routing.available
        assert "codex" in routing.available

    def test_validate_accepts_known_provider(self) -> None:
        routing = ModelRouting()
        # Should not raise
        routing.validate_provider("minimax")

    def test_validate_rejects_unknown_provider(self) -> None:
        routing = ModelRouting()
        with pytest.raises(ValueError, match="unknown model provider"):
            routing.validate_provider("gpt-99-ultra")
