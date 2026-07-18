"""RED tests for slice-13 PyPI packaging + docs.

Per SPEC §23 Part B bullet 9-10:
- pyproject.toml has setuptools backend
- pip install seharness installs CLI + telegram-bot + dashboard
- CHANGELOG.md exists with slice entries
- README.md has install/usage sections
- docs/user/ has install/configure/run/extend guides
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_pyproject_has_build_system() -> None:
    pp = _pyproject()
    assert "build-system" in pp
    bs = pp["build-system"]
    assert "requires" in bs
    assert "build-backend" in bs
    # Either setuptools or hatchling is acceptable.
    backend = str(bs["build-backend"])
    assert any(b in backend for b in ("setuptools", "hatchling"))


def test_pyproject_has_project_metadata() -> None:
    pp = _pyproject()
    project = pp["project"]
    assert project["name"] == "seharness"
    assert "version" in project
    assert "description" in project


def test_pyproject_has_runtime_dependencies() -> None:
    pp = _pyproject()
    project = pp["project"]
    deps = project.get("dependencies", [])
    assert isinstance(deps, list)
    # python-telegram-bot is the runtime transport.
    assert any("python-telegram-bot" in d for d in deps)
    # aiohttp for the dashboard server.
    assert any("aiohttp" in d for d in deps)
    # pyyaml for controller.yaml
    assert any("pyyaml" in d for d in deps)


def test_pyproject_console_scripts() -> None:
    pp = _pyproject()
    project = pp["project"]
    scripts = project.get("scripts", {})
    assert "seharness" in scripts
    assert "harness-telegram-bot" in scripts
    assert "harness-dashboard" in scripts


def test_changelog_exists() -> None:
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.exists()
    content = changelog.read_text()
    # Has at least one slice entry.
    assert "Slice 1" in content or "slice 1" in content.lower()
    # Has Slice 12 (the final SPEC slice).
    assert "Slice 12" in content or "slice 12" in content.lower()


def test_changelog_follows_keep_a_changelog_format() -> None:
    """Each slice has its own entry with a version + date."""
    changelog = (ROOT / "CHANGELOG.md").read_text()
    # Keep-a-changelog headers
    assert re.search(r"^##\s+\[", changelog, re.MULTILINE), "no [version] headers"
    # At least one version dated.
    assert re.search(r"\d{4}-\d{2}-\d{2}", changelog), "no dates"


def test_readme_has_quickstart() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "Install" in readme or "install" in readme
    assert "Usage" in readme or "usage" in readme


def test_readme_has_installation_command() -> None:
    readme = (ROOT / "README.md").read_text()
    assert "pip install" in readme


def test_docs_user_install_exists() -> None:
    assert (ROOT / "docs" / "user" / "install.md").exists()


def test_docs_user_configure_exists() -> None:
    assert (ROOT / "docs" / "user" / "configure.md").exists()


def test_docs_user_run_exists() -> None:
    assert (ROOT / "docs" / "user" / "run.md").exists()


def test_docs_user_extend_exists() -> None:
    assert (ROOT / "docs" / "user" / "extend.md").exists()


def test_example_controller_yaml_exists() -> None:
    """The factory from SPEC §21 needs a controller.yaml example."""
    example = ROOT / "examples" / "controller.yaml"
    assert example.exists()
    content = example.read_text()
    # Must have at least the standard slots.
    assert "ci_monitor" in content
    assert "task_executor" in content
    assert "run_ledger" in content


def test_example_env_exists() -> None:
    env = ROOT / ".env.example"
    assert env.exists()
    content = env.read_text()
    assert "TELEGRAM_BOT_TOKEN" in content
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in content
