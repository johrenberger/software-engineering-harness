"""Skill registry.

Discovers OpenClaw skill manifests in ``src/seharness/skills/<name>/SKILL.md``.
Each ``SKILL.md`` has YAML frontmatter:

    ---
    name: harness-feature
    description: Start a feature run
    allowed-tools: [seharness.cli.feature]
    ---

    # harness-feature
    ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True)
class SkillManifest:
    """Parsed frontmatter for an OpenClaw skill."""

    name: str
    description: str
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "allowed-tools": list(self.allowed_tools),
        }
        for k, v in self.extra:
            out[k] = v
        return out


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONT_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    out: dict[str, str] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


class SkillRegistry:
    """Immutable registry of OpenClaw skills.

    Skills live at ``src/seharness/skills/<name>/SKILL.md`` (inside the
    installed package).
    """

    def __init__(self, manifests: tuple[SkillManifest, ...], paths: dict[str, Path]) -> None:
        self._manifests = manifests
        self._paths = dict(paths)
        # Build name → manifest index
        self._by_name = {m.name: m for m in manifests}

    @classmethod
    def default(cls) -> SkillRegistry:
        """Discover skills from the installed package."""
        manifests: list[SkillManifest] = []
        paths: dict[str, Path] = {}

        # The skills ship inline as resources; we also check the filesystem
        # for development purposes.
        try:
            pkg_root = Path(resources.files("seharness.skills"))  # type: ignore[arg-type]
        except Exception:
            pkg_root = Path(__file__).resolve().parent

        if pkg_root.is_dir():
            for child in sorted(pkg_root.iterdir()):
                if not child.is_dir():
                    continue
                skill_md = child / "SKILL.md"
                if not skill_md.exists():
                    continue
                text = skill_md.read_text()
                fm = _parse_frontmatter(text)
                if "name" not in fm:
                    continue
                name = fm["name"]
                allowed_raw = fm.get("allowed-tools", "[]")
                # Very small list parser: accept [a, b] or [a,b]
                allowed_raw = allowed_raw.strip("[]")
                allowed = tuple(
                    p.strip().strip('"').strip("'") for p in allowed_raw.split(",") if p.strip()
                )
                extra = tuple(
                    (k, v)
                    for k, v in fm.items()
                    if k not in {"name", "description", "allowed-tools"}
                )
                manifest = SkillManifest(
                    name=name,
                    description=fm.get("description", ""),
                    allowed_tools=allowed,
                    extra=extra,
                )
                manifests.append(manifest)
                paths[name] = skill_md

        return cls(tuple(manifests), paths)

    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name.keys())

    def manifest(self, name: str) -> dict[str, Any]:
        if name not in self._by_name:
            raise KeyError(f"unknown skill: {name}")
        return self._by_name[name].to_dict()

    def path(self, name: str) -> Path:
        if name not in self._paths:
            raise KeyError(f"unknown skill: {name}")
        return self._paths[name]
