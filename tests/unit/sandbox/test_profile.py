"""RED tests for Cluster C, story C1: SandboxProfile Pydantic model.

Per SPEC §"Sandbox" (Cluster C) the profile captures:

- ``cwd`` — directory the sandboxed process starts in.
- ``allowed_paths`` — read/write paths the process may touch (relative
  to ``cwd`` or absolute).
- ``allowed_network_destinations`` — explicit allowlist for outbound
  network; empty list = no network at all.
- ``denied_env_vars`` — env var names that MUST be scrubbed from the
  inherited environment before launch.
- ``cpu_seconds`` — wall-clock budget (also a CPU budget on POSIX via
  ``RLIMIT_CPU``).
- ``memory_bytes`` — RSS cap (enforced via Docker mem_limit / RLIMIT_AS
  fallback).
- ``disk_bytes`` — write-byte cap.
- ``pids_limit`` — max number of processes (fork-bomb guard).
- ``image`` — container image (Docker path only).
- ``network_mode`` — Docker network mode (default "none").

Defaults are fail-closed: empty allowlist for paths and networks,
deny-by-default env scrub list (PATH, HOME, AWS_*, GITHUB_*, *_TOKEN,
*_SECRET, *_KEY).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError


class TestSandboxProfileImport:
    """``SandboxProfile`` is importable from ``seharness.sandbox``."""

    def test_profile_importable(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        assert SandboxProfile is not None


class TestSandboxProfileDefaults:
    """Defaults are fail-closed: nothing allowed unless opted in."""

    def test_default_cwd_is_cwd(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # Defaults to the process's current working directory; resolved.
        assert Path(p.cwd).resolve() == Path.cwd().resolve()

    def test_default_allowed_paths_is_empty(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        assert p.allowed_paths == ()

    def test_default_network_destinations_is_empty(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # Empty allowlist = no network allowed (fail-closed).
        assert p.allowed_network_destinations == ()

    def test_default_network_mode_is_none(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        assert p.network_mode == "none"

    def test_default_deny_env_includes_secrets(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # Built-in deny list contains high-risk env var patterns.
        assert "PATH" in p.denied_env_vars
        assert "HOME" in p.denied_env_vars
        assert "AWS_SECRET_ACCESS_KEY" in p.denied_env_vars
        assert "GITHUB_TOKEN" in p.denied_env_vars

    def test_default_time_budget_is_one_hour(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # Sensible default: 1h wall-clock ceiling.
        assert p.cpu_seconds == pytest.approx(3600.0)

    def test_default_memory_budget(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # 512 MiB default.
        assert p.memory_bytes == 512 * 1024 * 1024

    def test_default_disk_budget(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # 100 MiB default.
        assert p.disk_bytes == 100 * 1024 * 1024

    def test_default_pids_limit(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        # Fork-bomb guard: 64 processes max.
        assert p.pids_limit == 64

    def test_default_image(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        assert p.image == "python:3.13-slim"


class TestSandboxProfileValidation:
    """The profile rejects invalid values."""

    def test_negative_cpu_seconds_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(cpu_seconds=-1.0)

    def test_zero_cpu_seconds_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        # Must be strictly positive; 0 would mean "kill immediately".
        with pytest.raises(ValidationError):
            SandboxProfile(cpu_seconds=0.0)

    def test_negative_memory_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(memory_bytes=-1)

    def test_negative_pids_limit_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(pids_limit=-1)

    def test_relative_cwd_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(cwd="relative/path")

    def test_empty_image_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(image="")

    def test_invalid_network_mode_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(network_mode="internet")

    def test_extra_field_rejected(self) -> None:
        """``extra='forbid'`` catches typos at construction time."""
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(unknown_field=1)  # type: ignore[call-arg]


class TestSandboxProfileAllowedPaths:
    """Path allowlist is normalised and validated."""

    def test_relative_paths_converted_to_absolute(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile(allowed_paths=("subdir/",), cwd=str(tmp_path))
        # Resolved against cwd; should be absolute.
        assert Path(p.allowed_paths[0]).is_absolute()

    def test_absolute_paths_kept_absolute(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        target = tmp_path / "work"
        target.mkdir()
        p = SandboxProfile(
            allowed_paths=(str(target),),
            cwd=str(tmp_path),
        )
        assert Path(p.allowed_paths[0]).is_absolute()

    def test_empty_path_entry_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(allowed_paths=("",))


class TestSandboxProfileNetwork:
    """Network destinations must be hostnames, IPs, or CIDR ranges."""

    def test_simple_hostname_accepted(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile(allowed_network_destinations=("pypi.org",))
        assert "pypi.org" in p.allowed_network_destinations

    def test_ipv4_address_accepted(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile(allowed_network_destinations=("8.8.8.8",))
        assert "8.8.8.8" in p.allowed_network_destinations

    def test_cidr_range_accepted(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile(allowed_network_destinations=("10.0.0.0/8",))
        assert "10.0.0.0/8" in p.allowed_network_destinations

    def test_empty_destination_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(allowed_network_destinations=("",))

    def test_destination_with_whitespace_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(allowed_network_destinations=(" bad host ",))


class TestSandboxProfileEnvScrub:
    """Env var denylist supports exact names and case-insensitive patterns."""

    def test_extra_deny_added_to_defaults(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile(denied_env_vars=("MY_SECRET",))
        assert "MY_SECRET" in p.denied_env_vars
        # Defaults preserved.
        assert "PATH" in p.denied_env_vars

    def test_blank_deny_rejected(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(denied_env_vars=("",))


class TestSandboxProfileImmutability:
    """The profile is frozen; mutation is rejected."""

    def test_profile_is_frozen(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        p = SandboxProfile()
        with pytest.raises(ValidationError):
            p.cpu_seconds = 10.0  # type: ignore[misc]


class TestSandboxProfileRoundTrip:
    """The profile serialises/deserialises to JSON losslessly."""

    def test_round_trip_via_model_dump(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        original = SandboxProfile(
            cpu_seconds=120.0,
            memory_bytes=256 * 1024 * 1024,
            pids_limit=32,
            allowed_paths=("/tmp/work",),
            allowed_network_destinations=("pypi.org", "8.8.8.8"),
            denied_env_vars=("MY_SECRET",),
        )
        payload = original.model_dump()
        restored = SandboxProfile(**payload)
        assert restored == original

    def test_round_trip_via_json(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        original = SandboxProfile(cpu_seconds=60.0, pids_limit=16)
        restored = SandboxProfile.model_validate_json(original.model_dump_json())
        assert restored == original
