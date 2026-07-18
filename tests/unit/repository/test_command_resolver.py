"""RED tests for behavior 03 — Command resolver.

The resolver turns a :class:`RepositoryProfile` into concrete shell
command strings. It must:

* prefer repository-native tools (``uv run pytest`` for uv-managed
  projects, ``poetry run pytest`` for poetry projects, etc.)
* fall back to ``python -m pytest`` when no project runner is present
* respect detected tooling (``ruff``, ``mypy``) only if configured
* refuse to produce a command for an unknown gate (caller error)
* be deterministic (same profile in, same command out)
* be plugin-friendly (custom commands can be appended at runtime)
"""

from __future__ import annotations

from typing import Sequence

import pytest

from seharness.repository.discovery import (
    PackageManager,
    RepositoryProfile,
    ValidationCommand,
)
from seharness.repository.conventions import CommandResolver, Gate


def _profile(**kw: object) -> RepositoryProfile:
    defaults: dict[str, object] = {
        "name": "demo",
        "path": "/tmp/demo",
        "base_commit": "",
        "python_version_constraint": "",
        "package_manager": PackageManager.UV,
        "source_roots": ("src",),
        "test_roots": ("tests",),
        "framework_indicators": (),
        "validation_commands": (),
        "ci_workflows": (),
        "architecture_summary": "",
        "conventions": (),
        "baseline_validation_status": "unknown",
    }
    defaults.update(kw)
    return RepositoryProfile(**defaults)  # type: ignore[arg-type]


class TestTestCommandResolution:
    def test_uv_uses_uv_run(self) -> None:
        p = _profile(package_manager=PackageManager.UV)
        cmds = CommandResolver(p).resolve(Gate.TEST)
        assert ValidationCommand.TEST in cmds
        assert any("uv run" in c for c in cmds[ValidationCommand.TEST])

    def test_poetry_uses_poetry_run(self) -> None:
        p = _profile(package_manager=PackageManager.POETRY)
        cmds = CommandResolver(p).resolve(Gate.TEST)
        assert any("poetry run" in c for c in cmds[ValidationCommand.TEST])

    def test_setuptools_uses_python_m_pytest(self) -> None:
        p = _profile(package_manager=PackageManager.SETUPTOOLS)
        cmds = CommandResolver(p).resolve(Gate.TEST)
        assert any("python -m pytest" in c for c in cmds[ValidationCommand.TEST])

    def test_unknown_package_manager_falls_back(self) -> None:
        p = _profile(package_manager=PackageManager.UNKNOWN)
        cmds = CommandResolver(p).resolve(Gate.TEST)
        assert any("python -m pytest" in c for c in cmds[ValidationCommand.TEST])


class TestLintCommandResolution:
    def test_ruff_when_configured(self) -> None:
        p = _profile(conventions=("tool.ruff",))
        cmds = CommandResolver(p).resolve(Gate.LINT)
        assert ValidationCommand.LINT in cmds
        assert any("ruff" in c for c in cmds[ValidationCommand.LINT])

    def test_ruff_respects_package_manager(self) -> None:
        p = _profile(package_manager=PackageManager.UV, conventions=("tool.ruff",))
        cmds = CommandResolver(p).resolve(Gate.LINT)
        assert any("uv run ruff" in c for c in cmds[ValidationCommand.LINT])

    def test_no_lint_when_not_configured(self) -> None:
        p = _profile(conventions=())
        cmds = CommandResolver(p).resolve(Gate.LINT)
        # Gate present but empty list when no conventions detected.
        assert cmds[ValidationCommand.LINT] == ()


class TestTypeCheckCommandResolution:
    def test_mypy_when_configured(self) -> None:
        p = _profile(conventions=("tool.mypy",))
        cmds = CommandResolver(p).resolve(Gate.TYPE_CHECK)
        assert ValidationCommand.TYPE_CHECK in cmds
        assert any("mypy" in c for c in cmds[ValidationCommand.TYPE_CHECK])

    def test_no_mypy_when_not_configured(self) -> None:
        p = _profile(conventions=())
        cmds = CommandResolver(p).resolve(Gate.TYPE_CHECK)
        assert cmds[ValidationCommand.TYPE_CHECK] == ()


class TestFormatCommandResolution:
    def test_format_when_ruff_configured(self) -> None:
        p = _profile(conventions=("tool.ruff",))
        cmds = CommandResolver(p).resolve(Gate.FORMAT)
        assert ValidationCommand.FORMAT in cmds
        assert any("ruff format" in c for c in cmds[ValidationCommand.FORMAT])


class TestResolverDeterminism:
    def test_same_profile_same_commands(self) -> None:
        p = _profile(conventions=("tool.ruff", "tool.mypy"))
        a = CommandResolver(p).resolve(Gate.TEST, Gate.LINT)
        b = CommandResolver(p).resolve(Gate.TEST, Gate.LINT)
        assert a == b


class TestPluginFriendlyRegistry:
    """Custom gate commands can be registered at runtime (REFACTOR bullet)."""

    def test_register_custom_command(self) -> None:
        p = _profile()
        r = CommandResolver(p)
        r.register("smoke", ("python -m scripts.smoke",))
        assert "smoke" in r.gates
        cmds = r.resolve("smoke")
        assert cmds["smoke"] == ("python -m scripts.smoke",)

    def test_default_gates_available(self) -> None:
        r = CommandResolver(_profile())
        for g in (Gate.TEST, Gate.LINT, Gate.TYPE_CHECK, Gate.FORMAT):
            assert g in r.gates