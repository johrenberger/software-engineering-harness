"""Framework-neutral Python repository inspector.

Slice 3 implementation: reads ``pyproject.toml`` / ``setup.py`` /
lockfiles from a path on disk and returns a :class:`RepositoryProfile`
describing what the target looks like.

**Framework neutrality:** the inspector *records* framework indicators
(fastapi, flask, django, click, typer, …) but **never** branches
behavior on them. Validation commands are derived from the detected
package manager and ``[tool.*]`` configuration, never from which web
framework the code happens to import.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class PackageManager(StrEnum):
    """Detected Python package manager."""

    UV = "uv"
    POETRY = "poetry"
    PDM = "pdm"
    HATCH = "hatch"
    SETUPTOOLS = "setuptools"
    UNKNOWN = "unknown"


class FrameworkIndicator(StrEnum):
    """Framework/library *indicators* — recorded, never interpreted."""

    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    CLICK = "click"
    TYPER = "typer"
    PYDANTIC = "pydantic"
    SQLALCHEMY = "sqlalchemy"


class BaselineStatus(StrEnum):
    """Validation gate outcome vocabulary.

    Note: ``PASS = "pass"`` looks like a hardcoded credential to bandit
    but it is a status enum string, not a password. Inline ``nosec``
    marker keeps the warning out of the gate.
    """

    PASS = "pass"  # nosec B105 — status enum value, not a credential
    FAIL = "fail"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


# Mapping of import-name → indicator. Kept module-level (constant) so
# the inspector's matching is deterministic and easy to extend.
_IMPORT_TO_INDICATOR: dict[str, FrameworkIndicator] = {
    "fastapi": FrameworkIndicator.FASTAPI,
    "flask": FrameworkIndicator.FLASK,
    "django": FrameworkIndicator.DJANGO,
    "click": FrameworkIndicator.CLICK,
    "typer": FrameworkIndicator.TYPER,
    "pydantic": FrameworkIndicator.PYDANTIC,
    "sqlalchemy": FrameworkIndicator.SQLALCHEMY,
}

# Regex for capturing `import foo` / `from foo import …`.
_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)|import\s+([\w.]+))\b",
    re.MULTILINE,
)

# Top-level directories we never treat as source roots, even if they
# contain ``__init__.py``.
_NON_SOURCE_TOP_LEVELS = frozenset(
    {
        "src",
        "lib",
        "tests",
        "test",
        ".git",
        ".venv",
        "venv",
        "build",
        "dist",
        "__pycache__",
    }
)


def _first_segment(modname: str) -> str:
    """Return the top-level segment of a dotted module name."""
    return modname.split(".", 1)[0]


@dataclass(frozen=True)
class _Pyproject:
    """Minimal subset of pyproject.toml we care about."""

    tool_keys: frozenset[str]
    optional_dependency_groups: frozenset[str]
    requires_python: str
    build_backend: str


class RepositoryError(Exception):
    """Raised by :func:`inspect_repository` when the path is invalid."""


class _StrictModel(BaseModel):
    """Base model that forbids any keys not declared on the schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RepositoryProfile(_StrictModel):
    """Framework-neutral description of a Python repository.

    All 13 fields are required (use ``""`` / ``()`` / ``"unknown"`` for
    "not detected"). The model is frozen so downstream slices can rely on
    the values not changing mid-run.
    """

    name: str
    path: str
    base_commit: str
    python_version_constraint: str
    package_manager: PackageManager
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    framework_indicators: tuple[FrameworkIndicator, ...]
    validation_commands: tuple[str, ...]
    ci_workflows: tuple[str, ...]
    architecture_summary: str
    conventions: tuple[str, ...]
    baseline_validation_status: BaselineStatus


class BaselineSnapshot(_StrictModel):
    """Last-known outcome of one validation gate.

    Slice 7 will write these snapshots; slice 3 reads them. The shape is
    intentionally small so future gates can extend it without breaking
    older runs.
    """

    gate: str
    status: BaselineStatus
    captured_at: datetime
    commit: str
    duration_seconds: float
    summary: str


class ValidationCommand(StrEnum):
    """Canonical gate keys for the resolved command map."""

    TEST = "test"
    LINT = "lint"
    TYPE_CHECK = "type_check"
    FORMAT = "format"


# --- parsing helpers ---------------------------------------------------------


def _read_pyproject(path: Path) -> _Pyproject | None:
    if not (path / "pyproject.toml").is_file():
        return None
    try:
        loaded: object = tomllib.loads((path / "pyproject.toml").read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    if not isinstance(loaded, dict):
        return None
    raw: dict[str, object] = loaded

    project_obj = raw.get("project")
    project: dict[str, object] = project_obj if isinstance(project_obj, dict) else {}
    tool_obj = raw.get("tool")
    tool: dict[str, object] = tool_obj if isinstance(tool_obj, dict) else {}

    # Poetry-style: metadata lives under [tool.poetry]
    poetry_block = tool.get("poetry")
    if isinstance(poetry_block, dict):
        requires_python = str(poetry_block.get("python") or "")
    else:
        requires_python = str(project.get("requires-python") or "")

    tool_keys = frozenset(str(k) for k in tool)

    optional_groups_raw = project.get("optional-dependencies")
    if isinstance(optional_groups_raw, dict):
        optional_groups = frozenset(str(k) for k in optional_groups_raw)
    else:
        optional_groups = frozenset()

    build_system_obj = raw.get("build-system")
    build_system: dict[str, object] = build_system_obj if isinstance(build_system_obj, dict) else {}
    requires_obj = build_system.get("requires")
    requires = requires_obj if isinstance(requires_obj, list) else None
    build_backend = ""
    if requires:
        first = str(requires[0])
        # "hatchling" → "hatchling"; "poetry-core" → "poetry.core"; etc.
        build_backend = first.replace("-", ".")

    return _Pyproject(
        tool_keys=tool_keys,
        optional_dependency_groups=optional_groups,
        requires_python=requires_python,
        build_backend=build_backend,
    )


def _detect_package_manager(  # noqa: PLR0911 — ordered chain by design
    path: Path, pyproject: _Pyproject | None
) -> PackageManager:
    """Pick the most specific signal: lockfile > build backend > nothing."""
    if (path / "uv.lock").is_file():
        return PackageManager.UV
    if (path / "poetry.lock").is_file():
        return PackageManager.POETRY
    if (path / "pdm.lock").is_file():
        return PackageManager.PDM
    if (path / "hatch.toml").is_file():
        return PackageManager.HATCH
    if pyproject is not None and pyproject.build_backend:
        if pyproject.build_backend.startswith("hatchling"):
            return PackageManager.HATCH
        if pyproject.build_backend.startswith("poetry"):
            return PackageManager.POETRY
        if pyproject.build_backend.startswith("pdm"):
            return PackageManager.PDM
        if pyproject.build_backend.startswith("setuptools"):
            return PackageManager.SETUPTOOLS
    if (path / "setup.py").is_file() or (path / "setup.cfg").is_file():
        return PackageManager.SETUPTOOLS
    return PackageManager.UNKNOWN


def _dir_has_python_package(d: Path) -> bool:
    """True if ``d`` contains any Python package (``__init__.py``) at any depth."""
    return any(d.rglob("__init__.py"))


def _detect_source_roots(path: Path) -> tuple[str, ...]:
    """Find top-level Python package directories.

    Heuristic:

    * ``src/`` and ``lib/`` count as source roots when present and
      contain any Python file.
    * Otherwise, every top-level directory with a ``__init__.py``
      (direct child) counts as a flat-layout package.
    """
    roots: list[str] = []
    for candidate in ("src", "lib"):
        d = path / candidate
        if d.is_dir() and (any(d.rglob("*.py")) or _dir_has_python_package(d)):
            roots.append(candidate)
    for child in sorted(path.iterdir()):
        if not child.is_dir():
            continue
        if child.name in _NON_SOURCE_TOP_LEVELS:
            continue
        if (child / "__init__.py").is_file():
            roots.append(child.name)
    return tuple(roots)


def _detect_test_roots(path: Path) -> tuple[str, ...]:
    """Find top-level test directories (``tests`` or ``test``)."""
    roots: list[str] = []
    for name in ("tests", "test"):
        if (path / name).is_dir():
            roots.append(name)
    return tuple(roots)


def _detect_framework_indicators(
    path: Path, source_roots: tuple[str, ...]
) -> tuple[FrameworkIndicator, ...]:
    """Scan source roots for ``import`` / ``from … import`` statements.

    Only the top-level segment of each imported module is matched
    against :data:`_IMPORT_TO_INDICATOR`. Duplicates are removed and the
    result is sorted for determinism.
    """
    found: set[FrameworkIndicator] = set()
    for root in source_roots:
        root_path = path / root
        if not root_path.is_dir():
            continue
        for py_file in root_path.rglob("*.py"):
            try:
                text = py_file.read_text()
            except (OSError, UnicodeDecodeError):
                continue
            for match in _IMPORT_RE.finditer(text):
                modname = match.group(1) or match.group(2) or ""
                top = _first_segment(modname)
                if top in _IMPORT_TO_INDICATOR:
                    found.add(_IMPORT_TO_INDICATOR[top])
    return tuple(sorted(found, key=str))


def _detect_conventions(path: Path, pyproject: _Pyproject | None) -> tuple[str, ...]:
    """Convert detected [tool.*] sections into human-readable convention strings."""
    if pyproject is None:
        return ()
    seen: list[str] = []
    for key in sorted(pyproject.tool_keys):
        if key in {"ruff", "mypy", "pytest"}:
            seen.append(f"tool.{key}")
    if (path / ".github" / "workflows").is_dir():
        seen.append("ci.github-actions")
    return tuple(seen)


def _detect_ci_workflows(path: Path) -> tuple[str, ...]:
    wf = path / ".github" / "workflows"
    if not wf.is_dir():
        return ()
    return tuple(
        sorted(p.name for p in wf.iterdir() if p.is_file() and p.suffix in {".yml", ".yaml"})
    )


def _detect_architecture_summary(path: Path) -> str:
    """Tiny one-line architecture blurb based on layout."""
    if (path / "src").is_dir():
        return "src-layout Python package"
    if any((path / c / "__init__.py").is_file() for c in path.iterdir() if c.is_dir()):
        return "flat-layout Python package(s)"
    return "single-file Python project"


# --- public API --------------------------------------------------------------


def inspect_repository(path: Path) -> RepositoryProfile:
    """Inspect a Python repository on disk and return its profile.

    Raises :class:`RepositoryError` if ``path`` does not exist or is not
    a directory. Other I/O errors during scanning degrade gracefully:
    a corrupt ``pyproject.toml`` is treated as "no pyproject" rather
    than aborting the whole discovery.
    """
    if not path.exists():
        raise RepositoryError(f"repository path does not exist: {path}")
    if not path.is_dir():
        raise RepositoryError(f"repository path is not a directory: {path}")

    pyproject = _read_pyproject(path)
    package_manager = _detect_package_manager(path, pyproject)
    source_roots = _detect_source_roots(path)
    test_roots = _detect_test_roots(path)
    indicators = _detect_framework_indicators(path, source_roots)
    conventions = _detect_conventions(path, pyproject)
    ci = _detect_ci_workflows(path)
    architecture = _detect_architecture_summary(path)

    requires_python = pyproject.requires_python if pyproject is not None else ""

    return RepositoryProfile(
        name=path.name,
        path=str(path.resolve()),
        base_commit="",
        python_version_constraint=requires_python,
        package_manager=package_manager,
        source_roots=source_roots,
        test_roots=test_roots,
        framework_indicators=indicators,
        validation_commands=(),
        ci_workflows=ci,
        architecture_summary=architecture,
        conventions=conventions,
        baseline_validation_status=BaselineStatus.UNKNOWN,
    )


__all__ = [
    "BaselineSnapshot",
    "BaselineStatus",
    "FrameworkIndicator",
    "PackageManager",
    "RepositoryError",
    "RepositoryProfile",
    "ValidationCommand",
    "inspect_repository",
]
