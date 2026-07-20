"""Tests for ``scripts/check_version_drift.py``.

The drift checker is the safety net for cluster G story G9. It must
catch the common ways version drift creeps in:

* Bumped ``pyproject.toml`` but forgot ``__version__``.
* Bumped ``__version__`` but forgot ``pyproject.toml``.
* Added code but didn't cut a ``CHANGELOG.md`` entry.
* Wrote a header for the wrong version.

These tests run as part of the regular unit suite (path
``tests/unit/scripts/``) and additionally as a step in
``release.yml::verify-version``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add scripts/ to path so we can import the module.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import check_version_drift as m  # noqa: E402


@pytest.fixture
def tmp_project(tmp_path: Path) -> dict[str, Path]:
    """A tiny fake project root with matching versions."""
    pyproject = tmp_path / "pyproject.toml"
    init = tmp_path / "init.py"
    changelog = tmp_path / "CHANGELOG.md"

    pyproject.write_text(
        '[project]\nname = "fake"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    init.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [1.2.3] - 2026-01-01\n\n### Added\n- thing\n",
        encoding="utf-8",
    )
    return {
        "pyproject": pyproject,
        "init": init,
        "changelog": changelog,
    }


# ---------------------------------------------------------------------------
# Read functions
# ---------------------------------------------------------------------------


class TestReadPyprojectVersion:
    def test_round_trip(self, tmp_project: dict[str, Path]) -> None:
        assert m.read_pyproject_version(tmp_project["pyproject"]) == "1.2.3"

    def test_missing_version_key(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('[project]\nname = "fake"\n', encoding="utf-8")
        with pytest.raises(SystemExit):
            m.read_pyproject_version(pp)


class TestReadInitVersion:
    def test_double_quotes(self, tmp_project: dict[str, Path]) -> None:
        assert m.read_init_version(tmp_project["init"]) == "1.2.3"

    def test_single_quotes(self, tmp_path: Path) -> None:
        init = tmp_path / "i.py"
        init.write_text("__version__ = '9.9.9'\n", encoding="utf-8")
        assert m.read_init_version(init) == "9.9.9"

    def test_missing_init_file(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            m.read_init_version(tmp_path / "nope.py")

    def test_missing_assignment(self, tmp_path: Path) -> None:
        init = tmp_path / "i.py"
        init.write_text("# no version here\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            m.read_init_version(init)


class TestReadChangelogTopVersion:
    def test_unreleased_only(self, tmp_path: Path) -> None:
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("# C\n\n## [Unreleased]\n\nstuff\n", encoding="utf-8")
        assert m.read_changelog_top_version(cl) is None

    def test_first_released(self, tmp_project: dict[str, Path]) -> None:
        assert m.read_changelog_top_version(tmp_project["changelog"]) == "1.2.3"

    def test_missing_changelog(self, tmp_path: Path) -> None:
        assert m.read_changelog_top_version(tmp_path / "nope.md") is None

    def test_skips_released_below_unreleased(self, tmp_path: Path) -> None:
        """``## [Unreleased]`` is skipped, next header is read."""
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text(
            "# C\n\n## [Unreleased]\n\n## [2.0.0] - 2026-01-01\n",
            encoding="utf-8",
        )
        assert m.read_changelog_top_version(cl) == "2.0.0"


# ---------------------------------------------------------------------------
# check() — full integration
# ---------------------------------------------------------------------------


class TestCheck:
    def test_all_match(self, tmp_project: dict[str, Path]) -> None:
        rc = m.check(
            pyproject=tmp_project["pyproject"],
            init=tmp_project["init"],
            changelog=tmp_project["changelog"],
            expected=None,
        )
        assert rc == 0

    def test_drift_pyproject_vs_init(self, tmp_project: dict[str, Path]) -> None:
        tmp_project["pyproject"].write_text(
            '[project]\nname = "fake"\nversion = "1.2.4"\n',
            encoding="utf-8",
        )
        rc = m.check(
            pyproject=tmp_project["pyproject"],
            init=tmp_project["init"],
            changelog=tmp_project["changelog"],
            expected=None,
        )
        assert rc == 1

    def test_drift_init_vs_changelog(self, tmp_project: dict[str, Path]) -> None:
        tmp_project["init"].write_text('__version__ = "1.2.4"\n', encoding="utf-8")
        rc = m.check(
            pyproject=tmp_project["pyproject"],
            init=tmp_project["init"],
            changelog=tmp_project["changelog"],
            expected=None,
        )
        assert rc == 1

    def test_expected_version_match(self, tmp_project: dict[str, Path]) -> None:
        rc = m.check(
            pyproject=tmp_project["pyproject"],
            init=tmp_project["init"],
            changelog=tmp_project["changelog"],
            expected="1.2.3",
        )
        assert rc == 0

    def test_expected_version_mismatch(self, tmp_project: dict[str, Path]) -> None:
        rc = m.check(
            pyproject=tmp_project["pyproject"],
            init=tmp_project["init"],
            changelog=tmp_project["changelog"],
            expected="9.9.9",
        )
        assert rc == 1

    def test_changelog_only_unreleased_passes(self, tmp_path: Path) -> None:
        """A fresh repo with only ``## [Unreleased]`` should still pass."""
        pp = tmp_path / "pyproject.toml"
        pp.write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
        init = tmp_path / "i.py"
        init.write_text('__version__ = "0.1.0"\n', encoding="utf-8")
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("# C\n\n## [Unreleased]\n\n", encoding="utf-8")
        rc = m.check(pyproject=pp, init=init, changelog=cl, expected=None)
        assert rc == 0


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


class TestCLI:
    def test_main_no_args_against_repo(self) -> None:
        """Run ``python scripts/check_version_drift.py`` from repo root."""
        result = subprocess.run(
            [sys.executable, "scripts/check_version_drift.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Must not crash. May return 0 or 1 depending on whether the live
        # project state currently matches. Either way exit code must be
        # 0 or 1, not 2 (which signals a usage/file error).
        assert result.returncode in (0, 1), (
            f"unexpected exit code {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_main_with_expected_match(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
        init = tmp_path / "i.py"
        init.write_text('__version__ = "0.2.0"\n', encoding="utf-8")
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("# C\n\n## [0.2.0] - 2026-01-01\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_version_drift.py",
                "--pyproject",
                str(pp),
                "--init",
                str(init),
                "--changelog",
                str(cl),
                "--expected",
                "0.2.0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_main_with_expected_mismatch(self, tmp_path: Path) -> None:
        pp = tmp_path / "pyproject.toml"
        pp.write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
        init = tmp_path / "i.py"
        init.write_text('__version__ = "0.2.1"\n', encoding="utf-8")  # drift
        cl = tmp_path / "CHANGELOG.md"
        cl.write_text("# C\n\n## [0.2.0] - 2026-01-01\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_version_drift.py",
                "--pyproject",
                str(pp),
                "--init",
                str(init),
                "--changelog",
                str(cl),
                "--expected",
                "0.2.0",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "FAIL" in result.stderr
