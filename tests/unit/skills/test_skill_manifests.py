"""RED tests for slice-13 OpenClaw skill manifests.

Each skill is a directory with SKILL.md frontmatter (name, description,
allowed-tools). Tests verify the manifest format and discoverability.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _import_registry() -> object:
    from seharness.skills.registry import SkillRegistry

    return SkillRegistry


def test_registry_lists_required_skills() -> None:
    cls = _import_registry()
    reg = cls.default()
    names = set(reg.names())
    # Required per SPEC §23 Part A bullet 2:
    for name in (
        "harness-feature",
        "harness-status",
        "harness-runs",
        "harness-resume",
        "harness-cancel",
        "harness-pr",
        "harness-help",
        "harness-dashboard",
    ):
        assert name in names


def test_registry_each_skill_has_frontmatter(tmp_path: Path) -> None:
    cls = _import_registry()
    reg = cls.default()
    for name in reg.names():
        manifest = reg.manifest(name)
        assert "name" in manifest
        assert manifest["name"] == name
        assert "description" in manifest
        assert len(manifest["description"]) > 0


def test_registry_manifest_includes_allowed_tools() -> None:
    cls = _import_registry()
    reg = cls.default()
    for name in reg.names():
        manifest = reg.manifest(name)
        assert "allowed-tools" in manifest or "allowed_tools" in manifest


def test_registry_skill_directory_layout() -> None:
    cls = _import_registry()
    reg = cls.default()
    for name in reg.names():
        # Each skill points to a directory containing SKILL.md
        path = reg.path(name)
        assert path.name == "SKILL.md"
        assert path.exists()


def test_registry_names_are_unique() -> None:
    cls = _import_registry()
    reg = cls.default()
    names = list(reg.names())
    assert len(names) == len(set(names))


def test_registry_manifest_unknown_skill_raises() -> None:
    cls = _import_registry()
    reg = cls.default()
    with pytest.raises(KeyError):
        reg.manifest("harness-nonexistent")


def test_registry_path_unknown_skill_raises() -> None:
    cls = _import_registry()
    reg = cls.default()
    with pytest.raises(KeyError):
        reg.path("harness-nonexistent")


def test_registry_names_is_tuple() -> None:
    """Names must be tuple[str, ...] for immutability."""
    cls = _import_registry()
    reg = cls.default()
    assert isinstance(reg.names(), tuple)
