"""RED tests for behavior 02 — Framework-neutral repository discovery.

The inspector must classify a Python repository's source/test roots,
package manager, and framework indicators from disk — *without* assuming
any particular framework (FastAPI, Django, Flask). It records indicators
but never branches behavior on them.

Discovery is exercised against small fixture directories built with
``tmp_path``. The inspector reads:

* ``pyproject.toml`` (PEP 621 metadata, [tool.pytest], [tool.ruff],
  [tool.mypy], [project.optional-dependencies])
* ``setup.py`` / ``setup.cfg`` (fallback for legacy projects)
* ``uv.lock`` / ``poetry.lock`` / ``pdm.lock`` / ``hatch.toml``
* the ``src/`` and ``tests/`` directory layout
* framework indicator markers (presence of imports) — but only recorded,
  not interpreted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.repository.discovery import (
    FrameworkIndicator,
    PackageManager,
    inspect_repository,
)


# --- fixtures ---------------------------------------------------------------


def _write_pyproject(path: Path, body: str) -> None:
    (path / "pyproject.toml").write_text(body)


def _write_setup_py(path: Path, body: str) -> None:
    (path / "setup.py").write_text(body)


@pytest.fixture
def uv_project(tmp_path: Path) -> Path:
    _write_pyproject(
        tmp_path,
        '[project]\nname = "demo"\nrequires-python = ">=3.12"\n\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
    )
    (tmp_path / "uv.lock").write_text("# lockfile")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo").mkdir()
    (tmp_path / "src" / "demo" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    return tmp_path


@pytest.fixture
def poetry_project(tmp_path: Path) -> Path:
    _write_pyproject(
        tmp_path,
        '[tool.poetry]\nname = "demo"\npython = "^3.11"\n',
    )
    (tmp_path / "poetry.lock").write_text("# lockfile")
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    return tmp_path


@pytest.fixture
def setup_py_project(tmp_path: Path) -> Path:
    _write_setup_py(tmp_path, "from setuptools import setup\nsetup()\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    return tmp_path


@pytest.fixture
def framework_indicators_project(tmp_path: Path) -> Path:
    _write_pyproject(tmp_path, '[project]\nname = "demo"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app").mkdir()
    (tmp_path / "src" / "app" / "__init__.py").write_text("")
    (tmp_path / "src" / "app" / "web.py").write_text(
        "import fastapi  # noqa\nimport flask  # noqa\nimport django  # noqa\n"
    )
    (tmp_path / "tests").mkdir()
    return tmp_path


# --- discovery: core attributes --------------------------------------------


class TestInspectsRepositoryNameAndPath:
    def test_name_defaults_to_directory_basename(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert p.name == uv_project.name

    def test_path_is_absolute(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert Path(p.path).is_absolute()


class TestPackageManagerDetection:
    def test_uv_when_uv_lock_present(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert p.package_manager == PackageManager.UV

    def test_poetry_when_poetry_lock_present(self, poetry_project: Path) -> None:
        p = inspect_repository(poetry_project)
        assert p.package_manager == PackageManager.POETRY

    def test_setuptools_when_only_setup_py(self, setup_py_project: Path) -> None:
        p = inspect_repository(setup_py_project)
        assert p.package_manager == PackageManager.SETUPTOOLS

    def test_none_when_no_lockfile_and_no_setup_py(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "x.py").write_text("")
        p = inspect_repository(tmp_path)
        assert p.package_manager == PackageManager.UNKNOWN


class TestSourceRootDetection:
    def test_finds_src_layout(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert "src" in p.source_roots

    def test_finds_flat_layout(self, poetry_project: Path) -> None:
        p = inspect_repository(poetry_project)
        assert any(root.endswith("demo") for root in p.source_roots)

    def test_multiple_source_roots(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "x"\n')
        (tmp_path / "src" / "a").mkdir(parents=True)
        (tmp_path / "src" / "a" / "__init__.py").write_text("")
        (tmp_path / "lib" / "b").mkdir(parents=True)
        (tmp_path / "lib" / "b" / "__init__.py").write_text("")
        p = inspect_repository(tmp_path)
        assert "src" in p.source_roots
        assert "lib" in p.source_roots


class TestTestRootDetection:
    def test_finds_tests_directory(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert "tests" in p.test_roots

    def test_finds_test_singular(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "x"\n')
        (tmp_path / "test").mkdir()
        p = inspect_repository(tmp_path)
        assert "test" in p.test_roots

    def test_no_test_root(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "x.py").write_text("")
        p = inspect_repository(tmp_path)
        assert p.test_roots == ()


# --- framework neutrality --------------------------------------------------


class TestFrameworkNeutrality:
    """The inspector records indicators but does not steer behavior on them."""

    def test_no_framework_assumed_for_minimal_project(self, uv_project: Path) -> None:
        p = inspect_repository(uv_project)
        assert p.framework_indicators == ()

    def test_framework_indicators_are_recorded_for_app(
        self, framework_indicators_project: Path
    ) -> None:
        p = inspect_repository(framework_indicators_project)
        indicators = {str(i) for i in p.framework_indicators}
        assert FrameworkIndicator.FASTAPI in indicators
        assert FrameworkIndicator.FLASK in indicators
        assert FrameworkIndicator.DJANGO in indicators

    def test_profile_does_not_branch_on_framework(
        self, framework_indicators_project: Path
    ) -> None:
        """Validation commands must not change based on framework."""
        p = inspect_repository(framework_indicators_project)
        assert "uv run pytest" not in " ".join(p.validation_commands)
        # Validation commands, if any, are derived from pyproject — not from imports.
        for cmd in p.validation_commands:
            assert "fastapi" not in cmd
            assert "flask" not in cmd
            assert "django" not in cmd


class TestPyprojectToolDetection:
    def test_detects_ruff_config(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "x"\n\n[tool.ruff]\nline-length = 88\n')
        (tmp_path / "src" / "x.py").write_text("")
        p = inspect_repository(tmp_path)
        # ruff should appear in conventions, never in framework_indicators.
        assert any("ruff" in c.lower() for c in p.conventions)

    def test_detects_mypy_config(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "x"\n\n[tool.mypy]\nstrict = true\n')
        (tmp_path / "src" / "x.py").write_text("")
        p = inspect_repository(tmp_path)
        assert any("mypy" in c.lower() for c in p.conventions)

    def test_detects_pytest_config(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path, '[project]\nname = "x"\n\n[tool.pytest.ini_options]\ntestpaths = ["t"]\n'
        )
        (tmp_path / "src" / "x.py").write_text("")
        p = inspect_repository(tmp_path)
        assert any("pytest" in c.lower() for c in p.conventions)


# --- errors ----------------------------------------------------------------


class TestInspectsHandlesBadPath:
    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(Exception):
            inspect_repository(tmp_path / "nope")

    def test_file_instead_of_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "thing"
        f.write_text("not a dir")
        with pytest.raises(Exception):
            inspect_repository(f)