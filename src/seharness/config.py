"""Typed configuration models for the software engineering harness.

This module defines Pydantic v2 models that strictly validate harness
configuration. Unknown keys are rejected at every level so misconfigurations
fail loudly during ``seharness validate-config`` rather than silently during
a run.

Configuration precedence (highest wins):

    1. Command-line arguments
    2. Environment variables (``SEHARNESS_*``)
    3. Local configuration file (``seharness.local.yaml``)
    4. Repository ``harness.yaml``
    5. Built-in defaults (defined here)
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Re-exported for backward compatibility with slice 1 callers.
# Slice 4 promotes ProviderName to a StrEnum in domain/enums.py.
from seharness.domain.enums import ProviderName


class RuntimeProfile(StrEnum):
    """Runtime profile for the orchestrator.

    Profiles control adapter selection and fail-closed behaviour:

    - ``DEVELOPMENT``: local laptop, fast iteration. Stub adapters are
      allowed with a startup warning. Used by default when no profile
      is configured.
    - ``TEST``: test suite. Stubs allowed; no startup warnings (the
      test suite asserts specific behaviour, not stub warnings).
    - ``PRODUCTION``: live deployment. Stubs are REJECTED at startup;
      any critical adapter that resolves to a stub raises
      :class:`ConfigurationError` so the orchestrator never silently
      ships a passing-but-fake run.

    Cluster WP2 / story WP2.1. StrEnum so callers can compare against
    string literals (``profile == "production"``) without explicit
    conversion. New profiles must be added here AND in
    :func:`_validate_runtime_profile_adapters`.
    """

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"

# Provider IDs we know about today. New providers must be added here AND in
# ``ModelsConfig`` routing defaults before they can be used in routing.
_KNOWN_PROVIDERS: tuple[ProviderName, ...] = (ProviderName.MINIMAX, ProviderName.CODEX)

# Default routing fallback table. The constant is module-level so mypy can
# infer the precise enum element type without a runtime cast (which would
# produce a mutmut-false-positive equivalent mutant).
_DEFAULT_FALLBACK: dict[ProviderName, ProviderName] = {
    ProviderName.MINIMAX: ProviderName.CODEX,
    ProviderName.CODEX: ProviderName.MINIMAX,
}


class _StrictModel(BaseModel):
    """Base model that forbids any keys not declared on the schema."""

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


class HarnessSection(_StrictModel):
    """Top-level ``harness:`` block."""

    artifact_root: str = ".harness-runs"
    resume_enabled: bool = True
    fail_closed: bool = True
    # Cluster WP2: which runtime profile the orchestrator is operating
    # in. Defaults to DEVELOPMENT so existing callers (notebook /
    # local laptop runs) are unaffected. Production deployments must
    # set this explicitly to ``production`` to get the fail-closed
    # adapter validation; see :func:`validate_runtime_profile_adapters`.
    runtime_profile: RuntimeProfile = RuntimeProfile.DEVELOPMENT


class RepositoryConfig(_StrictModel):
    """Repository checkout and branch conventions."""

    clone_root: str = ".workspaces"
    base_branch: str = "main"
    branch_prefix: str = "ai/feature"


class ModelsConfig(_StrictModel):
    """Routing of workflow roles to model providers.

    Every slot must reference a provider from the known set, otherwise
    the configuration is rejected before a run starts.
    """

    planning: ProviderName = ProviderName.MINIMAX
    implementation: ProviderName = ProviderName.CODEX
    remediation: ProviderName = ProviderName.CODEX
    review: ProviderName = ProviderName.MINIMAX
    fallback: dict[ProviderName, ProviderName] = Field(
        default_factory=lambda: dict(_DEFAULT_FALLBACK)
    )


class ExecutionConfig(_StrictModel):
    """Execution knobs and retry budgets."""

    max_parallel_tasks: int = Field(default=1, ge=1, le=32)
    task_retry_limit: int = Field(default=2, ge=0, le=10)
    validation_repair_limit: int = Field(default=3, ge=0, le=10)
    review_repair_limit: int = Field(default=2, ge=0, le=10)
    ci_repair_limit: int = Field(default=2, ge=0, le=10)


class GitHubConfig(_StrictModel):
    """GitHub delivery controls. ``auto_merge`` is intentionally off by default."""

    create_pull_request: bool = True
    draft_pull_request: bool = True
    mark_ready_when_green: bool = True
    auto_merge: bool = False


class TelegramConfig(_StrictModel):
    """Telegram ingress controls. Disabled by default."""

    enabled: bool = False
    allowed_chat_ids: list[int] = Field(default_factory=list)


class HarnessConfig(_StrictModel):
    """The complete, fully-validated harness configuration."""

    harness: HarnessSection = Field(default_factory=HarnessSection)
    repository: RepositoryConfig = Field(default_factory=RepositoryConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class ModelRouting:
    """Registry of available model providers.

    Used at startup to validate that every routing slot and fallback
    pair references a real adapter. Currently statically known, but kept
    as a class so future adapter registration can extend it.
    """

    def __init__(self, available: tuple[str, ...] = _KNOWN_PROVIDERS) -> None:
        self._available: tuple[str, ...] = available

    @property
    def available(self) -> tuple[str, ...]:
        return self._available

    def validate_provider(self, provider: str) -> None:
        """Raise ``ValueError`` if ``provider`` is not registered.

        Used by configuration loaders to enforce that every routing
        slot and every fallback pair names a real adapter.
        """
        if provider not in self._available:
            raise ValueError(
                f"unknown model provider: {provider!r}. "
                f"Available providers: {', '.join(self._available)}"
            )


__all__ = [
    "ExecutionConfig",
    "GitHubConfig",
    "HarnessConfig",
    "HarnessSection",
    "ModelRouting",
    "ModelsConfig",
    "RepositoryConfig",
    "RuntimeProfile",
    "TelegramConfig",
    "ValidationError",
]
