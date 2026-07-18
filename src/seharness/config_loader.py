"""Configuration loader: reads YAML files, environment, and CLI overrides,
merges them in declared precedence order, then validates the merged result
through the strict Pydantic models in :mod:`seharness.config`.

Precedence (highest first):

    1. ``cli_overrides`` (typically from Typer)
    2. Environment variables prefixed with ``SEHARNESS_`` (dunder for nesting)
    3. Local configuration file (``seharness.local.yaml``)
    4. Repository ``harness.yaml``
    5. Built-in defaults (in :mod:`seharness.config`)

Environment variable mapping::

    SEHARNESS_HARNESS__ARTIFACT_ROOT         -> harness.artifact_root
    SEHARNESS_MODELS__PLANNING               -> models.planning
    SEHARNESS_EXECUTION__TASK_RETRY_LIMIT    -> execution.task_retry_limit
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from seharness.config import HarnessConfig
from seharness.exceptions import ConfigurationError

ENV_PREFIX = "SEHARNESS_"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file as a dict. Returns ``{}`` if the file does not exist.

    Raises :class:`ConfigurationError` on parse errors so the CLI can surface
    a friendly message instead of a stack trace.
    """
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigurationError(f"failed to parse YAML at {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"configuration file at {path} must be a mapping at the top level, "
            f"got {type(data).__name__}"
        )
    return data


def _coerce_env_value(raw: str) -> Any:
    """Coerce a string env value to a richer type where reasonable.

    Only ``"true"``/``"false"`` and integer strings are coerced. Everything
    else passes through as a string. Keeping coercion narrow avoids
    surprising the user with magic parsing.
    """
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    # Try integer coercion (no floats, no lists, no JSON — keep it predictable).
    try:
        return int(raw)
    except ValueError:
        return raw


def _split_env_key(key: str) -> list[str]:
    """Split a SEHARNESS_FOO__BAR__BAZ env key into ``['foo', 'bar', 'baz']``."""
    suffix = key[len(ENV_PREFIX) :].lower()
    return suffix.split("__")


def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
    """Set ``target[a][b][c] = value`` creating intermediate dicts as needed."""
    cursor: dict[str, Any] = target
    for piece in path[:-1]:
        if piece not in cursor or not isinstance(cursor[piece], dict):
            cursor[piece] = {}
        cursor = cursor[piece]
    cursor[path[-1]] = value


def _load_env() -> dict[str, Any]:
    """Read all SEHARNESS_* env vars into a nested dict."""
    out: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        parts = _split_env_key(key)
        if len(parts) < 2:
            # SEHARNESS_ alone or SEHARNESS_FOO without a section is invalid.
            raise ConfigurationError(
                f"environment variable {key} must include a section and key "
                f"(e.g. SEHARNESS_HARNESS__ARTIFACT_ROOT)"
            )
        _set_nested(out, parts, _coerce_env_value(raw))
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base`` returning a new dict.

    Dicts at the same path are merged recursively; any other type in
    ``override`` replaces the value in ``base``. The input dicts are not
    mutated.
    """
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(
    *,
    repo_yaml: Path | None = None,
    local_yaml: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    include_env: bool = True,
) -> HarnessConfig:
    """Load, merge, and validate configuration from all sources.

    Args:
        repo_yaml: Path to repository ``harness.yaml``. Missing files are
            tolerated silently (treated as ``{}``).
        local_yaml: Path to local override file (``seharness.local.yaml``).
            Missing files are tolerated.
        cli_overrides: Already-parsed CLI overrides as a nested dict.
        include_env: When ``False``, environment variables are ignored.
            Useful for deterministic tests.

    Returns:
        A fully-validated ``HarnessConfig`` instance.

    Raises:
        ConfigurationError: On parse errors, invalid env keys, or Pydantic
            validation failures (including unknown-key rejection).
    """
    merged: dict[str, Any] = {}

    # Layer 5 (defaults) — implicit in Pydantic model defaults
    # Layer 4: repository harness.yaml
    if repo_yaml is not None:
        merged = _deep_merge(merged, _load_yaml(repo_yaml))
    # Layer 3: local override file
    if local_yaml is not None:
        merged = _deep_merge(merged, _load_yaml(local_yaml))
    # Layer 2: environment variables
    if include_env:
        env_data = _load_env()
        if env_data:
            merged = _deep_merge(merged, env_data)
    # Layer 1: CLI overrides
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    try:
        return HarnessConfig.model_validate(merged)
    except ValidationError as e:
        raise ConfigurationError(str(e)) from e


__all__ = [
    "ENV_PREFIX",
    "ConfigurationError",
    "load_config",
]
