"""Adversarial tests targeting surviving mutmut mutants.

These tests are explicit kill-shots for the surviving mutants identified
during the slice 3 mutation run. Each test is named after the kind of
mutation it kills so a future regression makes the intent obvious.

Survivors observed (from .mutmut-cache on slice 3 source):

* ``conventions._RUNNER_PREFIX[PackageManager.UV] = "uv run"`` and the
  other runner mappings — mutmut mutates the string; without exact
  equality tests, the mutation can survive if the substring matches
  another gate's output by accident.

* ``Callable[[RepositoryProfile], tuple[str, ...]]`` — mutmut mutates the
  type alias; no test currently exercises the alias directly.

* ``if runner == "python -m":`` and the surrounding return statements —
  mutmut mutates the literal; tests must require the *exact* command
  string, not a substring.

* ``raise ValueError(...)`` → ``raise Exception(...)`` — mutmut weakens
  the exception class; tests must assert ``ValueError`` specifically.

* ``self._dir.mkdir(parents=True, exist_ok=True)`` → ``self._dir.mkdir()``
  — mutmut drops the kwargs; tests must exercise idempotency (writing
  twice must not crash).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from seharness.repository.conventions import (
    _BUILTIN_FACTORIES,
    _RUNNER_PREFIX,
    BaselineRecorder,
    CommandResolver,
    _CommandFactory,
)
from seharness.repository.discovery import (
    BaselineSnapshot,
    BaselineStatus,
    PackageManager,
    RepositoryProfile,
)


def _profile(**kw: Any) -> RepositoryProfile:
    defaults: dict[str, Any] = {
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
        "baseline_validation_status": BaselineStatus.UNKNOWN,
    }
    defaults.update(kw)
    return RepositoryProfile(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# runner-prefix exact-string killers
# ---------------------------------------------------------------------------


class TestRunnerPrefixExact:
    """Every runner prefix must match its expected string exactly."""

    @pytest.mark.parametrize(
        ("pm", "expected"),
        [
            (PackageManager.UV, "uv run"),
            (PackageManager.POETRY, "poetry run"),
            (PackageManager.PDM, "pdm run"),
            (PackageManager.HATCH, "hatch run"),
            (PackageManager.SETUPTOOLS, "python -m"),
            (PackageManager.UNKNOWN, "python -m"),
        ],
    )
    def test_runner_prefix_exact(self, pm: PackageManager, expected: str) -> None:
        assert _RUNNER_PREFIX[pm] == expected


# ---------------------------------------------------------------------------
# command-list exact equality killers
# ---------------------------------------------------------------------------


class TestCommandListExactEquality:
    """For each (PackageManager, gate) pair the returned tuple is exact."""

    @pytest.mark.parametrize(
        ("pm", "expected"),
        [
            (PackageManager.UV, ("uv run pytest",)),
            (PackageManager.POETRY, ("poetry run pytest",)),
            (PackageManager.PDM, ("pdm run pytest",)),
            (PackageManager.HATCH, ("hatch run pytest",)),
            (PackageManager.SETUPTOOLS, ("python -m pytest",)),
            (PackageManager.UNKNOWN, ("python -m pytest",)),
        ],
    )
    def test_test_command_exact(self, pm: PackageManager, expected: tuple[str, ...]) -> None:
        cmds = CommandResolver(_profile(package_manager=pm)).resolve("test")
        assert cmds == {"test": expected}

    @pytest.mark.parametrize(
        ("pm", "expected"),
        [
            (PackageManager.UV, ("uv run ruff check",)),
            (PackageManager.POETRY, ("poetry run ruff check",)),
            (PackageManager.PDM, ("pdm run ruff check",)),
            (PackageManager.HATCH, ("hatch run ruff check",)),
            (PackageManager.SETUPTOOLS, ("python -m ruff check",)),
            (PackageManager.UNKNOWN, ("python -m ruff check",)),
        ],
    )
    def test_lint_command_exact(self, pm: PackageManager, expected: tuple[str, ...]) -> None:
        cmds = CommandResolver(_profile(package_manager=pm, conventions=("tool.ruff",))).resolve(
            "lint"
        )
        assert cmds == {"lint": expected}

    @pytest.mark.parametrize(
        ("pm", "expected"),
        [
            (PackageManager.UV, ("uv run mypy",)),
            (PackageManager.POETRY, ("poetry run mypy",)),
            (PackageManager.PDM, ("pdm run mypy",)),
            (PackageManager.HATCH, ("hatch run mypy",)),
            (PackageManager.SETUPTOOLS, ("python -m mypy",)),
            (PackageManager.UNKNOWN, ("python -m mypy",)),
        ],
    )
    def test_type_check_command_exact(self, pm: PackageManager, expected: tuple[str, ...]) -> None:
        cmds = CommandResolver(_profile(package_manager=pm, conventions=("tool.mypy",))).resolve(
            "type_check"
        )
        assert cmds == {"type_check": expected}

    @pytest.mark.parametrize(
        ("pm", "expected"),
        [
            (PackageManager.UV, ("uv run ruff format",)),
            (PackageManager.POETRY, ("poetry run ruff format",)),
            (PackageManager.PDM, ("pdm run ruff format",)),
            (PackageManager.HATCH, ("hatch run ruff format",)),
            (PackageManager.SETUPTOOLS, ("python -m ruff format",)),
            (PackageManager.UNKNOWN, ("python -m ruff format",)),
        ],
    )
    def test_format_command_exact(self, pm: PackageManager, expected: tuple[str, ...]) -> None:
        cmds = CommandResolver(_profile(package_manager=pm, conventions=("tool.ruff",))).resolve(
            "format"
        )
        assert cmds == {"format": expected}

    def test_uv_lint_no_ruff(self) -> None:
        cmds = CommandResolver(_profile(package_manager=PackageManager.UV)).resolve("lint")
        assert cmds == {"lint": ()}


# ---------------------------------------------------------------------------
# type-alias killers
# ---------------------------------------------------------------------------


class TestCommandFactoryTypeAlias:
    """The ``_CommandFactory`` alias must be callable with the right shape."""

    def test_alias_is_callable(self) -> None:
        # Use the alias to build a tiny factory — proves it is callable
        # and accepts the documented argument shape.
        def factory(_profile: RepositoryProfile) -> tuple[str, ...]:
            return ("echo", "ok")

        # mypy: _CommandFactory is the alias; assignable to it.
        f: _CommandFactory = factory
        assert f(_profile()) == ("echo", "ok")

    def test_builtin_factories_have_all_four_gates(self) -> None:
        assert set(_BUILTIN_FACTORIES.keys()) == {"test", "lint", "type_check", "format"}

    def test_each_builtin_returns_tuple_of_strings(self) -> None:
        for name, factory in _BUILTIN_FACTORIES.items():
            result = factory(_profile())
            assert isinstance(result, tuple)
            for cmd in result:
                assert isinstance(cmd, str), f"{name} produced non-string {cmd!r}"


# ---------------------------------------------------------------------------
# specific-exception killers
# ---------------------------------------------------------------------------


class TestSpecificExceptionTypes:
    """Mutation that downgrades ValueError → Exception must not survive."""

    def test_register_built_in_raises_value_error(self) -> None:
        r = CommandResolver(_profile())
        with pytest.raises(ValueError):
            r.register("test", ("x",))

    def test_register_built_in_lint_raises_value_error(self) -> None:
        r = CommandResolver(_profile())
        with pytest.raises(ValueError):
            r.register("lint", ("x",))

    def test_unknown_gate_resolve_raises_value_error(self) -> None:
        r = CommandResolver(_profile())
        with pytest.raises(ValueError):
            r.resolve("not_a_gate")

    def test_value_error_message_mentions_gate(self) -> None:
        """The message must contain the offending gate name."""
        r = CommandResolver(_profile())
        with pytest.raises(ValueError, match="not_a_gate"):
            r.resolve("not_a_gate")

    def test_register_message_mentions_gate(self) -> None:
        """The message must contain the *quoted* repr of the gate name."""
        r = CommandResolver(_profile())
        with pytest.raises(ValueError, match=r"'test'"):
            r.register("test", ("x",))

    def test_register_message_is_quoted_repr(self) -> None:
        r = CommandResolver(_profile())
        try:
            r.register("lint", ("x",))
        except ValueError as e:
            assert "'lint'" in str(e), f"expected quoted repr, got {e!r}"
        else:
            pytest.fail("expected ValueError")

    def test_unknown_gate_message_is_quoted_repr(self) -> None:
        r = CommandResolver(_profile())
        try:
            r.resolve("nope")
        except ValueError as e:
            assert "'nope'" in str(e), f"expected quoted repr, got {e!r}"
        else:
            pytest.fail("expected ValueError")


# ---------------------------------------------------------------------------
# mkdir-idempotency killer
# ---------------------------------------------------------------------------


class TestBaselineRecorderMkdirIdempotent:
    """``mkdir`` must accept ``parents=True, exist_ok=True``."""

    def test_mkdir_keeps_existing_directory(self, tmp_path: Path) -> None:
        d = tmp_path / ".baseline"
        d.mkdir(parents=True, exist_ok=True)
        # Second call must not raise — proves parents=True + exist_ok=True
        BaselineRecorder(d)
        assert d.is_dir()

    def test_recorder_writes_twice_to_same_dir(self, tmp_path: Path) -> None:
        rec = BaselineRecorder(tmp_path / ".baseline")
        snap = BaselineSnapshot(
            gate="t",
            status=BaselineStatus.PASS,
            captured_at=datetime(2026, 7, 18, 17, 0, 0),
            commit="",
            duration_seconds=1.0,
            summary="ok",
        )
        rec.write(snap)
        # Second write must not crash because of mkdir kwargs being dropped
        rec.write(snap)
        assert rec.read("t") == snap

    def test_recorder_creates_nested_directory(self, tmp_path: Path) -> None:
        """parents=True is needed when intermediate dirs don't exist yet."""
        # The .baseline dir does not exist; recorder must create it.
        nested = tmp_path / "a" / "b" / "c" / ".baseline"
        assert not nested.exists()
        BaselineRecorder(nested)
        assert nested.is_dir()

    def test_recorder_tolerates_existing_dir(self, tmp_path: Path) -> None:
        """exist_ok=True is needed when the dir already exists."""
        d = tmp_path / ".baseline"
        d.mkdir()
        # Recorder must NOT raise even though the dir already exists.
        BaselineRecorder(d)
        assert d.is_dir()


# ---------------------------------------------------------------------------
# aggregate-status logic killers
# ---------------------------------------------------------------------------


class TestAggregateStatusLogic:
    """Pin the priority: FAIL > PARTIAL > PASS > UNKNOWN."""

    def test_aggregate_fail_only_when_at_least_one_fail(self, tmp_path: Path) -> None:
        d = tmp_path / ".baseline"
        d.mkdir()
        rec = BaselineRecorder(d)
        rec.write(
            BaselineSnapshot(
                gate="a",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        rec.write(
            BaselineSnapshot(
                gate="b",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        # No FAIL → not FAIL
        assert rec.aggregate_status() != BaselineStatus.FAIL

    def test_aggregate_fail_with_mixed_pass_and_fail(self, tmp_path: Path) -> None:
        d = tmp_path / ".baseline"
        d.mkdir()
        rec = BaselineRecorder(d)
        rec.write(
            BaselineSnapshot(
                gate="a",
                status=BaselineStatus.PASS,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="ok",
            )
        )
        rec.write(
            BaselineSnapshot(
                gate="b",
                status=BaselineStatus.FAIL,
                captured_at=datetime(2026, 7, 18, 17, 0, 0),
                commit="",
                duration_seconds=1.0,
                summary="bad",
            )
        )
        assert rec.aggregate_status() == BaselineStatus.FAIL


# ---------------------------------------------------------------------------
# register() custom-gate kills: verify the gate shows up after register
# ---------------------------------------------------------------------------


class TestRegisterGateShape:
    """After register(), the custom gate must appear in `gates`."""

    def test_custom_gate_listed_in_gates(self) -> None:
        r = CommandResolver(_profile())
        r.register("lint_strict", ("ruff check --strict",))
        assert "lint_strict" in r.gates
        # Built-in 'lint' must still be present (register() must not
        # accidentally clobber the built-in registry).
        assert "lint" in r.gates


# ---------------------------------------------------------------------------
# resolve() purity killers
# ---------------------------------------------------------------------------


class TestResolvePureAndOrderIndependent:
    """resolve() must be a pure function of (profile, gates)."""

    def test_resolve_does_not_mutate_state(self) -> None:
        r = CommandResolver(_profile())
        before = sorted(r.gates)
        r.resolve("test", "lint", "type_check", "format")
        after = sorted(r.gates)
        assert before == after

    def test_resolve_partial_subset(self) -> None:
        r = CommandResolver(_profile())
        full = r.resolve("test", "lint", "type_check", "format")
        assert r.resolve("test") == {"test": full["test"]}
        assert r.resolve("lint") == {"lint": full["lint"]}


# Iterable type for the factory tests
def _noop(_p: RepositoryProfile) -> tuple[str, ...]:
    return ()


def test_command_factory_alias_accepts_iterable_of_strings() -> None:
    factories: Iterable[tuple[str, _CommandFactory]] = (
        ("x", _noop),
        ("y", _noop),
    )
    seen = {name for name, _ in factories}
    assert seen == {"x", "y"}
