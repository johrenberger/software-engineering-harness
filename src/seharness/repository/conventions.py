"""Conventions layer: command resolution + baseline recording.

This module owns two pieces:

* :class:`CommandResolver` — turns a :class:`RepositoryProfile` into the
  concrete shell command list for each validation gate. Refuses to
  invent commands the profile didn't ask for. Plugin-friendly: callers
  can :meth:`CommandResolver.register` additional custom gates.

* :class:`BaselineRecorder` — persists the last-known validation status
  of each gate to ``<run-dir>/.baseline/<gate>.json`` using slice 2's
  :func:`atomic_write_json`. Slice 3 itself never runs subprocesses;
  slice 7 is the one that fills these snapshots.

Together they answer the REFACTOR bullet in the slice 3 spec
(``plugin-friendly detector interfaces``): the registry pattern keeps
the public API stable while letting future slices plug in their own
gates and detectors.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from seharness.artifacts.store import atomic_write_json

from .discovery import (
    BaselineSnapshot,
    BaselineStatus,
    PackageManager,
    RepositoryProfile,
    ValidationCommand,
)

# Command resolution --------------------------------------------------------


# Mapping from PackageManager → command prefix.
_RUNNER_PREFIX: Mapping[PackageManager, str] = {
    PackageManager.UV: "uv run",
    PackageManager.POETRY: "poetry run",
    PackageManager.PDM: "pdm run",
    PackageManager.HATCH: "hatch run",
    PackageManager.SETUPTOOLS: "python -m",
    PackageManager.UNKNOWN: "python -m",
}

# Type alias for the small per-gate command factory used below.
_CommandFactory = Callable[[RepositoryProfile], tuple[str, ...]]


def _runner(package_manager: PackageManager) -> str:
    """Return the canonical command prefix for the detected package manager."""
    return _RUNNER_PREFIX[package_manager]


class Gate:
    """String gate keys.

    The four canonical gates from :class:`ValidationCommand` are always
    present; custom gates can be added via
    :meth:`CommandResolver.register`. We use plain strings (not enums)
    for custom gates so callers don't have to extend an enum to add
    their own.
    """

    TEST = ValidationCommand.TEST.value
    LINT = ValidationCommand.LINT.value
    TYPE_CHECK = ValidationCommand.TYPE_CHECK.value
    FORMAT = ValidationCommand.FORMAT.value


def _test_command(profile: RepositoryProfile) -> tuple[str, ...]:
    runner = _runner(profile.package_manager)
    return (f"{runner} pytest",)


def _lint_command(profile: RepositoryProfile) -> tuple[str, ...]:
    if "tool.ruff" not in profile.conventions:
        return ()
    runner = _runner(profile.package_manager)
    return (f"{runner} ruff check",)


def _type_check_command(profile: RepositoryProfile) -> tuple[str, ...]:
    if "tool.mypy" not in profile.conventions:
        return ()
    runner = _runner(profile.package_manager)
    return (f"{runner} mypy",)


def _format_command(profile: RepositoryProfile) -> tuple[str, ...]:
    if "tool.ruff" not in profile.conventions:
        return ()
    runner = _runner(profile.package_manager)
    return (f"{runner} ruff format",)


# Built-in command factories indexed by gate name.
_BUILTIN_FACTORIES: Mapping[str, _CommandFactory] = {
    Gate.TEST: _test_command,
    Gate.LINT: _lint_command,
    Gate.TYPE_CHECK: _type_check_command,
    Gate.FORMAT: _format_command,
}


class CommandResolver:
    """Turn a :class:`RepositoryProfile` into shell commands per gate.

    The resolver is deliberately stateless apart from the registered
    custom gates: ``resolve()`` is pure, so the same profile always
    produces the same commands (covered by TestResolverDeterminism).
    """

    def __init__(self, profile: RepositoryProfile) -> None:
        self._profile = profile
        # Built-in gates always present.
        self._factories: dict[str, _CommandFactory] = dict(_BUILTIN_FACTORIES)
        self._custom_commands: dict[str, tuple[str, ...]] = {}

    # -- gate registry ------------------------------------------------------

    @property
    def gates(self) -> tuple[str, ...]:
        """All available gate names (built-ins + registered custom gates)."""
        seen: set[str] = set()
        for k in self._factories:
            seen.add(k)
        for k in self._custom_commands:
            seen.add(k)
        return tuple(sorted(seen))

    def register(self, gate: str, commands: Iterable[str]) -> None:
        """Register a custom gate with its fixed command list.

        Built-in gates cannot be replaced: callers that need different
        commands for a built-in gate must register a *new* gate name
        (e.g. ``smoke`` instead of ``test``).
        """
        if gate in _BUILTIN_FACTORIES:
            raise ValueError(f"cannot replace built-in gate {gate!r}; use a different name")
        self._custom_commands[gate] = tuple(commands)

    # -- resolution ---------------------------------------------------------

    def resolve(self, *gates: str) -> dict[str, tuple[str, ...]]:
        """Resolve the requested gates to their command strings.

        Unknown gate names raise ``ValueError`` — the caller mistyped
        or forgot to :meth:`register` a custom gate.
        """
        out: dict[str, tuple[str, ...]] = {}
        for gate in gates:
            if gate in self._custom_commands:
                out[gate] = self._custom_commands[gate]
                continue
            factory = self._factories.get(gate)
            if factory is not None:
                out[gate] = factory(self._profile)
                continue
            raise ValueError(f"unknown gate: {gate!r}")
        return out


# Baseline recording --------------------------------------------------------


class BaselineRecorder:
    """Read/write :class:`BaselineSnapshot` files in ``baseline_dir``.

    Callers pass the directory that *already is* the baseline directory
    (typically ``<run-dir>/.baseline/``). The recorder does not invent
    nested paths — keeping the layout explicit makes slice 7's writer
    trivial and prevents drift between read and write paths.
    """

    def __init__(self, baseline_dir: Path) -> None:
        self._dir = baseline_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- write/read ---------------------------------------------------------

    def _path_for(self, gate: str) -> Path:
        return self._dir / f"{gate}.json"

    def write(self, snapshot: BaselineSnapshot) -> None:
        """Persist ``snapshot`` to ``<dir>/<gate>.json`` atomically."""
        payload = snapshot.model_dump(mode="json")
        atomic_write_json(self._path_for(snapshot.gate), payload)

    def read(self, gate: str) -> BaselineSnapshot | None:
        """Load the snapshot for ``gate``, or ``None`` if no baseline exists."""
        path = self._path_for(gate)
        if not path.is_file():
            return None
        payload = path.read_text()
        return BaselineSnapshot.model_validate_json(payload)

    def load_all(self) -> dict[str, BaselineSnapshot]:
        """Load every snapshot present in the baseline directory."""
        out: dict[str, BaselineSnapshot] = {}
        for path in sorted(self._dir.glob("*.json")):
            gate = path.stem
            snap = BaselineSnapshot.model_validate_json(path.read_text())
            out[gate] = snap
        return out

    # -- aggregation --------------------------------------------------------

    def aggregate_status(self) -> BaselineStatus:
        """Combine all snapshots into one status.

        Rules (most-severe wins, ignoring UNKNOWN so a missing baseline
        doesn't drag a healthy project down):

        * any FAIL → FAIL
        * else any PARTIAL → PARTIAL
        * else any PASS → PASS
        * else (no snapshots, or all UNKNOWN) → UNKNOWN
        """
        snaps = self.load_all()
        if not snaps:
            return BaselineStatus.UNKNOWN
        statuses = {s.status for s in snaps.values() if s.status != BaselineStatus.UNKNOWN}
        if BaselineStatus.FAIL in statuses:
            return BaselineStatus.FAIL
        if BaselineStatus.PARTIAL in statuses:
            return BaselineStatus.PARTIAL
        if BaselineStatus.PASS in statuses:
            return BaselineStatus.PASS
        return BaselineStatus.UNKNOWN


__all__ = [
    "BaselineRecorder",
    "BaselineSnapshot",
    "CommandResolver",
    "Gate",
]
