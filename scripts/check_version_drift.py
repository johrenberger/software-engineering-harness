#!/usr/bin/env python3
"""G9: cross-file version drift check.

Fails (exit 1) when any of the following disagree:

1. ``pyproject.toml`` version (``[project] version = "X.Y.Z"``)
2. ``src/seharness/__init__.py`` ``__version__ = "X.Y.Z"``
3. ``CHANGELOG.md`` top-level ``## [X.Y.Z] - <date>`` header

Usage:

* No args: read the live files and ensure 1 == 2 == 3.
* ``--expected X.Y.Z``: ensure all three equal ``X.Y.Z`` (used by the
  release workflow on tag push, where the tag is the source of truth).
* ``--pyproject PATH`` / ``--init PATH`` / ``--changelog PATH``: override
  locations (used by tests).

Stdlib only — no third-party imports. Runs in CI as part of
``release.yml::verify-version`` and in pre-commit (when wired).

Exit codes:

* 0 — all three match.
* 1 — at least one mismatch (CI-visible).
* 2 — usage error.

Design choices:

* We deliberately parse ``pyproject.toml`` with ``tomllib`` rather than
  regex to avoid drift if the project table reorders.
* The CHANGELOG header is matched with a permissive regex (``## [X.Y.Z]``
  with optional suffix) — different people write different headers and we
  don't want a trivial date or punctuation change to fail the gate.
* The drift check does NOT validate semver format — that's PEP 440's job
  and ``setuptools_scm`` would do it on build. We only check cross-file
  consistency.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


DEFAULT_PYPROJECT = Path("pyproject.toml")
DEFAULT_INIT = Path("src/seharness/__init__.py")
DEFAULT_CHANGELOG = Path("CHANGELOG.md")


def read_pyproject_version(path: Path) -> str:
    """Read ``[project].version`` from a ``pyproject.toml``."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    try:
        version = data["project"]["version"]
    except KeyError as e:
        raise SystemExit(f"{path}: missing [project].version ({e})") from e
    if not isinstance(version, str):
        raise SystemExit(f"{path}: [project].version is not a string: {version!r}")
    return version.strip()


def read_init_version(path: Path) -> str:
    """Read ``__version__ = "..."`` from an ``__init__.py``."""
    if not path.exists():
        raise SystemExit(f"{path}: not found")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"""__version__\s*=\s*['"]([^'"]+)['"]""", text)
    if match is None:
        raise SystemExit(f"{path}: __version__ assignment not found")
    return match.group(1).strip()


def read_changelog_top_version(path: Path) -> str | None:
    """Return the top-most ``## [X.Y.Z] - <date>`` header in CHANGELOG.md.

    Returns ``None`` if no such header is found (CHANGELOG might be
    missing or contain only ``## [Unreleased]`` blocks, which is fine
    during development). Returns ``None`` on parse error so callers can
    distinguish "no header yet" from "wrong header".
    """
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        # ``## [X.Y.Z]`` with optional date / extra suffix.
        match = re.match(r"^##\s+\[([^\]]+)\]", line)
        if match is None:
            continue
        version = match.group(1).strip()
        # Skip Unreleased — that's the in-progress section, not a release.
        if version.lower() == "unreleased":
            continue
        return version
    return None


def check(
    *,
    pyproject: Path,
    init: Path,
    changelog: Path,
    expected: str | None,
) -> int:
    """Check version drift. Returns 0 on success, 1 on drift."""
    pyproject_v = read_pyproject_version(pyproject)
    init_v = read_init_version(init)
    changelog_v = read_changelog_top_version(changelog)

    failures: list[str] = []

    if expected is not None:
        if pyproject_v != expected:
            failures.append(
                f"pyproject.toml version {pyproject_v!r} != expected {expected!r}"
            )
        if init_v != expected:
            failures.append(
                f"{init} __version__ {init_v!r} != expected {expected!r}"
            )
        if changelog_v is not None and changelog_v != expected:
            failures.append(
                f"CHANGELOG.md top header {changelog_v!r} != expected {expected!r}"
            )
    else:
        # No expected: just ensure all three agree (when CHANGELOG has one).
        if changelog_v is not None and changelog_v != pyproject_v:
            failures.append(
                f"CHANGELOG.md top header {changelog_v!r} != "
                f"pyproject.toml {pyproject_v!r}"
            )
        if init_v != pyproject_v:
            failures.append(
                f"{init} __version__ {init_v!r} != "
                f"pyproject.toml {pyproject_v!r}"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1

    parts = [
        f"pyproject.toml={pyproject_v}",
        f"{init}={init_v}",
    ]
    if changelog_v is not None:
        parts.append(f"CHANGELOG.md={changelog_v}")
    else:
        parts.append("CHANGELOG.md=(no released header)")
    print(f"OK: {' / '.join(parts)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check cross-file version drift in the project.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=DEFAULT_PYPROJECT,
        help="path to pyproject.toml (default: %(default)s)",
    )
    parser.add_argument(
        "--init",
        type=Path,
        default=DEFAULT_INIT,
        help="path to __init__.py with __version__ (default: %(default)s)",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
        help="path to CHANGELOG.md (default: %(default)s)",
    )
    parser.add_argument(
        "--expected",
        type=str,
        default=None,
        help=(
            "expected version (e.g. '0.2.0'). When set, the checker "
            "verifies all three files equal this version rather than "
            "checking pairwise agreement."
        ),
    )
    args = parser.parse_args(argv)
    try:
        return check(
            pyproject=args.pyproject,
            init=args.init,
            changelog=args.changelog,
            expected=args.expected,
        )
    except SystemExit as exc:
        # SystemExit from helpers — convert to non-zero exit + write to stderr.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())