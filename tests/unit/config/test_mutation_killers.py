"""Adversarial tests that pin every default value and error message in the
typed config layer.

These tests exist explicitly to kill mutation-survivors from mutmut's
first pass on src/seharness/config.py. The mutants that survived were
all constant-default substitutions (e.g. swapping ``1`` for ``0`` or
``"minimax"`` for ``"codex"``). Each test below targets one survivor
category, with the precise literal value asserted.

If any default in src/seharness/config.py is changed, at least one of
these tests must be updated -- and that update must be deliberate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
from pydantic import ValidationError

from seharness.config import (
    _KNOWN_PROVIDERS,
    ExecutionConfig,
    GitHubConfig,
    HarnessConfig,
    HarnessSection,
    ModelRouting,
    ModelsConfig,
    RepositoryConfig,
    TelegramConfig,
    _StrictModel,
)


# ---------------------------------------------------------------------
# 1. Survivors #8, #9 -- toggling `extra="forbid"` to `extra="allow"`
#    should break strict rejection. The following tests assert that
#    `extra="forbid"` is enforced at every level and is not silently
#    disabled.
# ---------------------------------------------------------------------
class TestExtraForbidEnforced:
    """Every section must reject unknown keys with a ValidationError."""

    @pytest.mark.parametrize(
        "model_class, payload",
        [
            (HarnessConfig, {"unknown_root_key": 1}),
            (HarnessSection, {"unknown_harness_key": 1}),
            (RepositoryConfig, {"unknown_repo_key": 1}),
            (ModelsConfig, {"unknown_models_key": 1}),
            (ExecutionConfig, {"unknown_exec_key": 1}),
            (GitHubConfig, {"unknown_github_key": 1}),
            (TelegramConfig, {"unknown_telegram_key": 1}),
        ],
    )
    def test_rejects_unknown_section_key(
        self, model_class: type[_StrictModel], payload: dict[str, object]
    ) -> None:
        with pytest.raises(ValidationError) as ei:
            model_class.model_validate(payload)
        # The validation error must reference the offending key, proving
        # it came from `extra="forbid"`, not from a type error.
        msg = str(ei.value)
        assert "extra" in msg.lower(), f"expected ValidationError to mention 'extra'; got: {msg!r}"

    def test_strictmodel_model_config_is_forbid(self) -> None:
        """The base class itself must keep ``extra='forbid'`` configured.

        Mutating this string would disable strict rejection globally.
        """
        assert _StrictModel.model_config["extra"] == "forbid", (
            "_StrictModel.model_config['extra'] must remain 'forbid'; "
            f"got {_StrictModel.model_config.get('extra')!r}"
        )

    def test_strictmodel_keeps_validate_assignment(self) -> None:
        """``validate_assignment`` enables post-init validation; turning
        it off would let invalid assignments slip through silently."""
        assert _StrictModel.model_config.get("validate_assignment") is True

    def test_strictmodel_is_not_frozen(self) -> None:
        """``frozen`` controls mutability. It must NOT change to True
        without an explicit schema review."""
        assert _StrictModel.model_config.get("frozen") is False, (
            f"_StrictModel must not be frozen; got {_StrictModel.model_config.get('frozen')!r}"
        )


# ---------------------------------------------------------------------
# 2. Survivor #31 -- ModelRouting fallback dict default values.
# ---------------------------------------------------------------------
class TestModelsConfigFallbackDictExact:
    """Pin the literal default for ``fallback``."""

    def test_default_fallback_is_minimax_to_codex_and_codex_to_minimax(self) -> None:
        models = ModelsConfig()
        assert models.fallback == {"minimax": "codex", "codex": "minimax"}, (
            f"unexpected default fallback: {models.fallback!r}"
        )

    def test_default_fallback_keys_match_known_providers(self) -> None:
        models = ModelsConfig()
        assert set(models.fallback.keys()) == set(_KNOWN_PROVIDERS)

    def test_default_fallback_values_are_known_providers(self) -> None:
        models = ModelsConfig()
        for k, v in models.fallback.items():
            assert k in _KNOWN_PROVIDERS, f"key {k!r} not in known providers"
            assert v in _KNOWN_PROVIDERS, (
                f"fallback for {k!r} resolves to {v!r} which is not a known provider"
            )

    def test_no_provider_maps_to_itself_by_default(self) -> None:
        """By default a provider must NOT fall back to itself; mutating
        the default dict to e.g. ``minimax->minimax`` must be caught."""
        models = ModelsConfig()
        for k, v in models.fallback.items():
            assert v != k, f"fallback for {k!r} is {v!r}; a provider may not fall back to itself"


# ---------------------------------------------------------------------
# 3. Survivors #40, 43, 44, 47, 48, 51, 52, 55, 56 --
#    ExecutionConfig retry-budget defaults. Mutations like
#    ``default=1`` -> ``default=0`` (or 2 -> 1) must be caught.
# ---------------------------------------------------------------------
class TestExecutionConfigDefaultsExact:
    """Every retry-budget default is a critical invariant. Each test
    pins the literal value the field must default to. If you change one,
    you must change the test on purpose."""

    def test_max_parallel_tasks_default_is_one(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.max_parallel_tasks == 1, (
            f"max_parallel_tasks default must be 1 (sequential), got {cfg.max_parallel_tasks!r}"
        )

    def test_task_retry_limit_default_is_two(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.task_retry_limit == 2, (
            f"task_retry_limit default must be 2, got {cfg.task_retry_limit!r}"
        )

    def test_validation_repair_limit_default_is_three(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.validation_repair_limit == 3, (
            f"validation_repair_limit default must be 3, got {cfg.validation_repair_limit!r}"
        )

    def test_review_repair_limit_default_is_two(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.review_repair_limit == 2, (
            f"review_repair_limit default must be 2, got {cfg.review_repair_limit!r}"
        )

    def test_ci_repair_limit_default_is_two(self) -> None:
        cfg = ExecutionConfig()
        assert cfg.ci_repair_limit == 2, (
            f"ci_repair_limit default must be 2, got {cfg.ci_repair_limit!r}"
        )

    def test_all_execution_defaults_distinct_from_bounds(self) -> None:
        """None of the defaults should equal the upper bound; this catches
        mutants like ``default=1`` -> ``default=32`` (max int bound)."""
        cfg = ExecutionConfig()
        for fld in ("max_parallel_tasks", "task_retry_limit"):
            value = getattr(cfg, fld)
            # Each field type info carries ge/le constraints.
            finfo = ExecutionConfig.model_fields[fld]
            assert value != finfo.metadata[1].le, (
                f"{fld} default {value!r} equals upper bound -- likely a default-default mutant"
            )

    def test_max_parallel_tasks_bounds_are_exactly_1_and_32(self) -> None:
        """Targeted kill for mutant #40 (``le=32`` -> ``le=33``)."""
        finfo = ExecutionConfig.model_fields["max_parallel_tasks"]
        assert finfo.metadata[0].ge == 1
        assert finfo.metadata[1].le == 32

    def test_task_retry_limit_bounds_are_exactly_0_and_10(self) -> None:
        """Targeted kill for mutants #43 (``ge=0`` -> ``ge=1``) and #44 (``le=10`` -> ``le=11``)."""
        finfo = ExecutionConfig.model_fields["task_retry_limit"]
        assert finfo.metadata[0].ge == 0
        assert finfo.metadata[1].le == 10

    def test_validation_repair_limit_bounds_are_exactly_0_and_10(self) -> None:
        """Targeted kill for mutants #47, #48."""
        finfo = ExecutionConfig.model_fields["validation_repair_limit"]
        assert finfo.metadata[0].ge == 0
        assert finfo.metadata[1].le == 10

    def test_review_repair_limit_bounds_are_exactly_0_and_10(self) -> None:
        """Targeted kill for mutants #51, #52."""
        finfo = ExecutionConfig.model_fields["review_repair_limit"]
        assert finfo.metadata[0].ge == 0
        assert finfo.metadata[1].le == 10

    def test_ci_repair_limit_bounds_are_exactly_0_and_10(self) -> None:
        """Targeted kill for mutants #55, #56."""
        finfo = ExecutionConfig.model_fields["ci_repair_limit"]
        assert finfo.metadata[0].ge == 0
        assert finfo.metadata[1].le == 10

    def test_max_parallel_tasks_rejects_value_at_le_33(self) -> None:
        """Direct behaviour check for mutant #40. If ``le=33`` were
        sneakily introduced, a value of 33 would still be accepted.
        """
        with pytest.raises(ValidationError):
            ExecutionConfig(max_parallel_tasks=33)

    def test_repair_limits_reject_value_at_ge_1_when_ge_should_be_0(self) -> None:
        """Targeted kill for mutants #43, #47, #51, #55 which flip
        ``ge=0`` -> ``ge=1``. The retry-budget limits must accept 0
        because each slice can opt out of retries by setting 0.
        """
        for fld in (
            "task_retry_limit",
            "validation_repair_limit",
            "review_repair_limit",
            "ci_repair_limit",
        ):
            cfg = ExecutionConfig(**{fld: 0})  # must not raise
            assert getattr(cfg, fld) == 0


# ---------------------------------------------------------------------
# 4. Survivors #72, #74 -- HarnessConfig field declarations.
#    Mutating ``Field(default_factory=ExecutionConfig)`` -> a constant
#    would yield empty sections. Mutating
#    ``Field(default_factory=TelegramConfig)`` -> ``None`` (the actual
#    bug we once shipped) would make telegram either opaque or rejected.
# ---------------------------------------------------------------------
class TestHarnessConfigFieldFactoriesAreFactories:
    """Each HarnessConfig field must have a callable default_factory
    that produces a fully-populated section."""

    @pytest.mark.parametrize(
        "field_name, section_class",
        [
            ("harness", HarnessSection),
            ("repository", RepositoryConfig),
            ("models", ModelsConfig),
            ("execution", ExecutionConfig),
            ("github", GitHubConfig),
            ("telegram", TelegramConfig),
        ],
    )
    def test_field_default_factory_yields_section_instance(
        self, field_name: str, section_class: type[_StrictModel]
    ) -> None:
        finfo = HarnessConfig.model_fields[field_name]
        factory = finfo.default_factory
        assert factory is not None, f"{field_name} must have a default_factory"
        callable_factory = cast(Callable[[], Any], factory)
        produced = callable_factory()
        # mypy: section_class is narrowed via isinstance; runtime check
        # confirms structural identity.
        assert isinstance(produced, section_class), (
            f"{field_name}!r default_factory produced {type(produced).__name__}, "
            f"expected {section_class.__name__}"
        )

    def test_telegram_field_does_not_declare_none(self) -> None:
        """Regression: an earlier draft typed `telegram: TelegramConfig = None`,
        which mypy --strict rejected and would cause a Pydantic ValidationError
        at runtime. This test pins the field annotation back to a real
        factory-backed default.
        """
        finfo = HarnessConfig.model_fields["telegram"]
        # The annotation string should reference TelegramConfig, not 'None'.
        ann = str(finfo.annotation)
        assert "TelegramConfig" in ann, f"telegram annotation {ann!r} lost its type"
        assert "None" not in ann.replace("NoneType", "").split("|"), (
            f"telegram annotation {ann!r} should not be None"
        )
        # The default_factory must be present and produce a TelegramConfig.
        assert finfo.default_factory is not None
        factory = cast(Callable[[], Any], finfo.default_factory)
        assert isinstance(factory(), TelegramConfig)


# ---------------------------------------------------------------------
# 5. Survivors #78, #79, #80 -- validate_provider error message body.
# ---------------------------------------------------------------------
class TestModelRoutingErrorMessageExact:
    """Pin the exact text of the ``validate_provider`` error so that
    casual string mutations are caught."""

    def test_validate_provider_error_includes_unknown_provider_name(self) -> None:
        r = ModelRouting()
        with pytest.raises(ValueError) as ei:
            r.validate_provider("not-a-provider")
        msg = str(ei.value)
        assert "not-a-provider" in msg, f"error must quote the unknown provider; got: {msg!r}"

    def test_validate_provider_error_prefix_is_unknown_provider(self) -> None:
        r = ModelRouting()
        with pytest.raises(ValueError) as ei:
            r.validate_provider("bogus")
        msg = str(ei.value)
        assert msg.startswith("unknown model provider:"), (
            f"error must start with 'unknown model provider:'; got: {msg!r}"
        )

    def test_validate_provider_error_lists_available_providers(self) -> None:
        r = ModelRouting()
        with pytest.raises(ValueError) as ei:
            r.validate_provider("bogus")
        msg = str(ei.value)
        # Every known provider must be listed.
        for p in _KNOWN_PROVIDERS:
            assert p in msg, f"available providers list missing {p!r}; got: {msg!r}"

    def test_validate_provider_accepts_every_known_provider(self) -> None:
        r = ModelRouting()
        for p in _KNOWN_PROVIDERS:
            r.validate_provider(p)  # must not raise

    def test_validate_provider_error_uses_comma_space_separator(self) -> None:
        """Targeted kill for mutants #79, #80 (join string and prefix
        wrapping). The entire suffix after ``unknown model provider:
        'bogus'. `` must equal ``Available providers: minimax, codex``.
        """
        r = ModelRouting()
        with pytest.raises(ValueError) as ei:
            r.validate_provider("bogus")
        msg = str(ei.value)
        expected_suffix = "Available providers: minimax, codex"
        assert msg.endswith(expected_suffix), (
            f"expected message to end with {expected_suffix!r}; got: {msg!r}"
        )


# ---------------------------------------------------------------------
# 6. Coverage of HarnessSection default values too (sister to ExecutionConfig).
# ---------------------------------------------------------------------
class TestHarnessSectionDefaultsExact:
    """HarnessSection also has default values worth pinning."""

    def test_default_artifact_root_is_dot_harness_runs(self) -> None:
        assert HarnessSection().artifact_root == ".harness-runs"

    def test_default_resume_enabled_is_true(self) -> None:
        assert HarnessSection().resume_enabled is True

    def test_default_fail_closed_is_true(self) -> None:
        assert HarnessSection().fail_closed is True


# ---------------------------------------------------------------------
# 7. Coverage of RepositoryConfig / TelegramConfig defaults.
# ---------------------------------------------------------------------
class TestOtherSectionDefaults:
    def test_repository_default_clone_root(self) -> None:
        assert RepositoryConfig().clone_root == ".workspaces"

    def test_repository_default_base_branch_is_main(self) -> None:
        assert RepositoryConfig().base_branch == "main"

    def test_repository_default_branch_prefix_startswith_ai(self) -> None:
        assert RepositoryConfig().branch_prefix.startswith("ai/")

    def test_telegram_default_enabled_is_false(self) -> None:
        assert TelegramConfig().enabled is False

    def test_telegram_default_allowed_chat_ids_is_empty_list(self) -> None:
        assert TelegramConfig().allowed_chat_ids == []
