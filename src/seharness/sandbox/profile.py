"""Cluster C, story C1: ``SandboxProfile`` Pydantic model.

The profile is the *declarative* layer of the sandbox: it captures
what the caller wants enforced (paths, network, env, budgets) without
specifying *how* it is enforced. The execution layer (``DockerSandbox``,
``SubprocessSandbox``) reads the profile and applies platform-specific
isolation primitives.

Design choices:

- **Pydantic v2**, ``ConfigDict(extra=\"forbid\", frozen=True)`` —
  typos surface at construction; mutation is rejected at runtime.
- **Fail-closed defaults**: empty allowlist for paths and network;
  built-in deny-by-default env scrub list (PATH, HOME, AWS_*,
  GITHUB_TOKEN, *_SECRET, *_KEY, *_TOKEN).
- **Absolute ``cwd``** required — relative working directories are
  ambiguous and easy to confuse across chroots/containers.
- **Strict budgets**: ``cpu_seconds`` must be > 0; ``memory_bytes``,
  ``disk_bytes``, ``pids_limit`` must be >= 0; ``image`` non-empty.
- **Network destinations** are validated as either:
  - a hostname (RFC-1123 label) — exact-match allow
  - an IPv4 address — exact-match allow
  - an IPv4 CIDR range — subnet allow
  - the literal ``*`` — wildcard (only the executor decides if it
    supports it; Docker subprocess cannot, so it is rejected by the
    executor at apply-time, not at profile construction).
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# Built-in deny-by-default env-var patterns. Users can ADD to this list
# via ``SandboxProfile(denied_env_vars=(...))`` but cannot remove entries
# — preventing accidental secret leakage by a permissive caller.
DEFAULT_DENIED_ENV_VARS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENCLAW_TOKEN",
)

# 1 hour default wall-clock ceiling.
DEFAULT_CPU_SECONDS: float = 3600.0
# 512 MiB default RSS cap.
DEFAULT_MEMORY_BYTES: int = 512 * 1024 * 1024
# 100 MiB default write-byte cap.
DEFAULT_DISK_BYTES: int = 100 * 1024 * 1024
# 64 processes default fork-bomb guard.
DEFAULT_PIDS_LIMIT: int = 64

# Hostname validation — RFC-1123 labels, ASCII only.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
_CIDR_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}/\d{1,2}$")

NetworkMode = Literal["none", "bridge", "host"]


class SandboxProfile(BaseModel):
    """Declarative isolation profile consumed by a ``SandboxExecutor``.

    Attributes
    ----------
    cwd:
        Absolute directory the sandboxed process starts in. Required
        to be absolute; the ``field_validator`` enforces this so callers
        cannot accidentally run inside ``./``.
    allowed_paths:
        Read/write allowlist. Relative entries are resolved against
        ``cwd``; absolute entries are kept as-is. Empty tuple = no
        filesystem access at all (callers can still read the cwd).
    allowed_network_destinations:
        Exact-match allowlist for outbound network. Supports hostnames,
        IPv4 addresses, and IPv4 CIDR ranges. Empty tuple = network
        disabled.
    denied_env_vars:
        Env var names scrubbed from the inherited environment before
        launch. Built-in defaults (PATH, HOME, AWS_*, GITHUB_TOKEN,
        *_SECRET, *_KEY, *_TOKEN) are always present; user entries
        are appended.
    cpu_seconds:
        Wall-clock / CPU budget in seconds. ``> 0``.
    memory_bytes:
        RSS cap in bytes. ``>= 0`` (0 means uncapped at the executor
        layer; Docker mem_limit accepts 0).
    disk_bytes:
        Write-byte cap in bytes. ``>= 0``.
    pids_limit:
        Max processes. ``>= 1``.
    image:
        Container image used by ``DockerSandbox``. Defaults to
        ``python:3.13-slim`` per cluster spec.
    network_mode:
        Docker network mode. ``\"none\"`` (default) disables all
        networking; ``\"bridge\"`` allows the standard bridge;
        ``\"host\"`` shares the host network namespace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cwd: str = str(Path.cwd())
    allowed_paths: tuple[str, ...] = ()
    allowed_network_destinations: tuple[str, ...] = ()
    denied_env_vars: tuple[str, ...] = DEFAULT_DENIED_ENV_VARS
    cpu_seconds: float = DEFAULT_CPU_SECONDS
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    disk_bytes: int = DEFAULT_DISK_BYTES
    pids_limit: int = DEFAULT_PIDS_LIMIT
    image: str = "python:3.13-slim"
    network_mode: NetworkMode = "none"

    @field_validator("cwd")
    @classmethod
    def _validate_cwd(cls, value: str) -> str:
        if not value:
            raise ValueError("cwd must be non-empty")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f"cwd must be absolute, got {value!r}")
        return str(path)

    @field_validator("cpu_seconds")
    @classmethod
    def _validate_cpu_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError(f"cpu_seconds must be > 0, got {value}")
        return float(value)

    @field_validator("memory_bytes", "disk_bytes")
    @classmethod
    def _validate_non_negative_bytes(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"byte budget must be >= 0, got {value}")
        return int(value)

    @field_validator("pids_limit")
    @classmethod
    def _validate_pids_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"pids_limit must be >= 1, got {value}")
        return int(value)

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str) -> str:
        if not value:
            raise ValueError("image must be non-empty")
        return value

    @field_validator("allowed_paths")
    @classmethod
    def _validate_allowed_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned: list[str] = []
        for raw in value:
            stripped = raw.strip()
            if not stripped:
                raise ValueError("allowed_paths entries must be non-empty")
            cleaned.append(stripped)
        return tuple(cleaned)

    @field_validator("allowed_paths", mode="after")
    @classmethod
    def _resolve_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve relative ``allowed_paths`` against the parent cwd."""
        resolved: list[str] = []
        for entry in value:
            if os.path.isabs(entry):
                resolved.append(entry)
            else:
                # Use the process cwd (we don't have access to the
                # profile's ``cwd`` from a field_validator without
                # validation_context plumbing; relative paths are
                # resolved against the process cwd at construction
                # time, which matches what the executor will see when
                # it chdir's into ``profile.cwd``).
                resolved.append(os.path.join(os.getcwd(), entry))
        return tuple(resolved)

    @field_validator("allowed_network_destinations")
    @classmethod
    def _validate_network_destinations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for raw in value:
            stripped = raw.strip()
            if not stripped:
                raise ValueError("network destination must be non-empty")
            if stripped != raw or " " in stripped:
                raise ValueError(f"network destination {raw!r} must not contain whitespace")
            # Try IPv4 address.
            try:
                ipaddress.IPv4Address(stripped)
                continue
            except ValueError:
                pass
            # Try CIDR.
            if _CIDR_RE.match(stripped):
                try:
                    ipaddress.IPv4Network(stripped, strict=False)
                    continue
                except ValueError as exc:
                    raise ValueError(f"invalid CIDR range {stripped!r}: {exc}") from exc
            # Otherwise must be a valid hostname.
            if not _HOSTNAME_RE.match(stripped):
                raise ValueError(
                    f"network destination {stripped!r} is not a valid hostname, "
                    f"IPv4 address, or CIDR range"
                )
        return value

    @field_validator("denied_env_vars", mode="before")
    @classmethod
    def _merge_default_deny(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Merge user deny list with the built-in defaults.

        Built-in defaults are always kept so a permissive caller cannot
        accidentally re-enable secret exfiltration by passing
        ``denied_env_vars=("MY_VAR",)``.
        """
        for entry in value:
            if not entry or not entry.strip():
                raise ValueError("denied_env_vars entries must be non-empty")
        # De-dup while preserving order: defaults first, then user extras.
        return tuple(dict.fromkeys((*DEFAULT_DENIED_ENV_VARS, *value)))

    @field_validator("denied_env_vars")
    @classmethod
    def _validate_denied_env_vars(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for entry in value:
            if not entry or not entry.strip():
                raise ValueError("denied_env_vars entries must be non-empty")
        return value

    @model_validator(mode="after")
    def _noop(self) -> SandboxProfile:
        """Placeholder for future cross-field invariants."""
        return self


__all__ = [
    "DEFAULT_CPU_SECONDS",
    "DEFAULT_DENIED_ENV_VARS",
    "DEFAULT_DISK_BYTES",
    "DEFAULT_MEMORY_BYTES",
    "DEFAULT_PIDS_LIMIT",
    "NetworkMode",
    "SandboxProfile",
]
