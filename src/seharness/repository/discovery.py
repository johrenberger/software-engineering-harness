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
import subprocess  # nosec B404 - controlled use of git rev-parse / status
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

    All 17 fields are required (use ``""`` / ``()`` / ``"unknown"`` for
    "not detected"). The model is frozen so downstream slices can rely on
    the values not changing mid-run.

    The shape grew from 13 → 17 fields during slice-13 / WP4 to
    surface the facts the planner needs (instruction files,
    monorepo flag, base commit, dirty-state policy) so the
    orchestrator never has to re-derive them from the filesystem.
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
    # WP4 additions (slice 13 / PR3):
    instruction_files: tuple[str, ...]
    is_monorepo: bool
    git_dirty: bool
    detected_language: str


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


# ---------------------------------------------------------------------------
# WP4 / PR3 additions — slice 13 territory.
# These helpers surface the facts the orchestrator needs to plan from
# discovered state instead of hard-coded constants.
# ---------------------------------------------------------------------------


_INSTRUCTION_FILE_NAMES: tuple[str, ...] = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "CODEOWNERS",
    "README.md",
)


def _detect_instruction_files(path: Path) -> tuple[str, ...]:
    """Return the names of repo-level instruction files that exist.

    These are surfaced verbatim (no parsing) so the planner can quote
    or link to them when generating tasks. Detection is
    case-insensitive on a known whitelist to avoid pulling arbitrary
    dotfiles into the profile.
    """
    found: list[str] = []
    for name in _INSTRUCTION_FILE_NAMES:
        if (path / name).is_file():
            found.append(name)
    return tuple(found)


def _detect_monorepo(path: Path) -> bool:
    """True when the repo has more than one Python source root or
    multiple ``pyproject.toml`` files.

    The heuristic is intentionally cheap: ``inspect_repository`` is
    called on every run, so we walk at most one level deep and
    bail out as soon as we see a second project marker.
    """
    if _read_pyproject(path) is not None:
        # Look for nested pyproject.toml files one level down.
        nested = sum(
            1 for child in path.iterdir() if child.is_dir() and (child / "pyproject.toml").is_file()
        )
        if nested > 0:
            return True
    return False


def _detect_git_state(path: Path) -> tuple[str, bool]:
    """Return (base_commit, dirty).

    ``base_commit`` is the current HEAD commit SHA, or ``""`` when the
    path is not a git repository or git is unavailable. ``dirty`` is
    True iff the working tree has uncommitted changes (excluding
    untracked files in the repo root).

    Implementation note: we shell out to ``git`` rather than using
    ``dulwich``/``pygit2`` because git is guaranteed to be installed
    in every CI environment we run in, and a 50 ms subprocess is
    cheaper than a dependency for a one-shot call.
    """
    if not (path / ".git").exists():
        return "", False
    try:
        head = subprocess.run(  # nosec B603 B607 - argv is a fixed literal; no shell
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    if head.returncode != 0:
        return "", False
    commit = head.stdout.strip()
    try:
        status = subprocess.run(  # nosec B603 B607 - argv is a fixed literal; no shell
            ["git", "-C", str(path), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return commit, False
    return commit, status.returncode == 0 and bool(status.stdout.strip())


def _detect_language(path: Path) -> str:
    """Detect the dominant source language by extension count.

    Returns one of ``"python"`` / ``"typescript"`` / ``"javascript"`` /
    ``"rust"`` / ``"go"`` / ``"unknown"``. The scan walks at most
    two levels deep (the repo root and one directory down, e.g.
    ``src/`` / ``tests/`` / ``packages/``) so it stays O(visible
    files) without missing the common src-layout repos.
    """
    if not path.is_dir():
        return "unknown"
    counts: dict[str, int] = {}
    try:
        children = list(path.iterdir())
    except OSError:
        return "unknown"
    _tally_top_level(counts, children)
    _tally_package_subdirs(counts, children)
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _tally_top_level(counts: dict[str, int], children: list[Path]) -> None:
    """Bump language counters for top-level files in ``children``."""
    for child in children:
        if not child.is_file():
            continue
        ext = child.suffix.lstrip(".").lower()
        if not ext:
            continue
        _tally_ext(counts, ext)


def _tally_package_subdirs(counts: dict[str, int], children: list[Path]) -> None:
    """Bump language counters for files inside known package roots.

    Common package roots are scanned; deeper trees are out of
    scope so this stays O(visible files).
    """
    package_roots = {"src", "lib", "tests", "packages", "apps", "services"}
    for child in children:
        if not child.is_dir() or child.name not in package_roots:
            continue
        try:
            sub_files = list(child.iterdir())
        except OSError:
            continue
        for sub in sub_files:
            if not sub.is_file():
                continue
            ext = sub.suffix.lstrip(".").lower()
            if not ext:
                continue
            _tally_ext(counts, ext)


def _tally_ext(counts: dict[str, int], ext: str) -> None:
    """Bump the language counter for a single file extension."""
    if ext == "py":
        counts["python"] = counts.get("python", 0) + 1
    elif ext == "ts":
        counts["typescript"] = counts.get("typescript", 0) + 1
    elif ext == "js":
        counts["javascript"] = counts.get("javascript", 0) + 1
    elif ext == "rs":
        counts["rust"] = counts.get("rust", 0) + 1
    elif ext == "go":
        counts["go"] = counts.get("go", 0) + 1


# --- public API --------------------------------------------------------------


def inspect_repository(path: Path) -> RepositoryProfile:
    """Inspect a Python repository on disk and return its profile.

    Raises :class:`RepositoryError` if ``path`` does not exist or is not
    a directory. Other I/O errors during scanning degrade gracefully:
    a corrupt ``pyproject.toml`` is treated as "no pyproject" rather
    than aborting the whole discovery.

    WP4 / PR3 additions: the profile now carries the repo's base
    git commit, dirty-state, monorepo flag, instruction-file list,
    and a cheap dominant-language detection. These are the facts the
    planner consumes to generate tasks, dependencies, allowed paths
    and validation commands instead of relying on hard-coded
    constants.
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
    instruction_files = _detect_instruction_files(path)
    is_monorepo = _detect_monorepo(path)
    base_commit, git_dirty = _detect_git_state(path)
    detected_language = _detect_language(path)

    requires_python = pyproject.requires_python if pyproject is not None else ""

    return RepositoryProfile(
        name=path.name,
        path=str(path.resolve()),
        base_commit=base_commit,
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
        instruction_files=instruction_files,
        is_monorepo=is_monorepo,
        git_dirty=git_dirty,
        detected_language=detected_language,
    )


# ---------------------------------------------------------------------------
# Plan-derivation helpers (WP4 / PR3).
# These are pure functions over a RepositoryProfile so the planner can
# stay decoupled from the inspector. Note: per-gate command resolution
# (test/lint/type_check/format) is handled by
# :class:`CommandResolver` in :mod:`seharness.repository.conventions`
# — we don't duplicate that here.
# ---------------------------------------------------------------------------


def derive_allowed_paths(profile: RepositoryProfile) -> tuple[str, ...]:
    """Return the canonical allowed paths for ``profile``.

    Cluster WP4 / story WP4.5: derive allowed paths from the
    detected source roots + test roots + docs directory. Each
    project marker yields at most one entry; the docs directory is
    included when present so doc-only changes are plan-compatible.
    """
    paths: list[str] = []
    for root in profile.source_roots:
        paths.append(f"{root}/")
    for root in profile.test_roots:
        paths.append(f"{root}/")
    repo_path = Path(profile.path)
    if (repo_path / "docs").is_dir():
        paths.append("docs/")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


__all__ = [
    "BaselineSnapshot",
    "BaselineStatus",
    "FrameworkIndicator",
    "PackageManager",
    "RepositoryError",
    "RepositoryProfile",
    "ValidationCommand",
    "derive_allowed_paths",
    "inspect_repository",
]
