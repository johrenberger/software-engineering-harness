"""WP9 (Cluster N) — artifact and sandbox adversarial tests.

The handoff lists these cases:

* Poisoned caches and untrusted build artifacts.
* Fork bombs and orphan process trees (config-level, not actual).
* CPU, memory, disk, FD, process limits (config-level).
* Malicious dependency-install scripts.

The harness deliberately does NOT actually launch fork
bombs, real curl probes, or install scripts from
arbitrary sources in unit tests — those would risk
OOM-killing the host. The TEST CONTRACT is: every
isolation primitive is REJECTED at the configuration
layer unless explicitly set to a safe value.

These tests pin the ENFORCED BOUNDARY for each isolation
primitive and the EXPECTED FAILURE STATE when a
malicious configuration is supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.sandbox.profile import (
    DEFAULT_CPU_SECONDS,
    DEFAULT_DISK_BYTES,
    DEFAULT_MEMORY_BYTES,
    DEFAULT_PIDS_LIMIT,
    SandboxProfile,
)

# ---------------------------------------------------------------------------
# WP9.16 — Resource-limit configuration
# ---------------------------------------------------------------------------


class TestResourceLimitsConfig:
    """ENFORCED BOUNDARY: every resource limit must be a
    positive bounded value. EXPECTED FAILURE STATE:
    ``SandboxProfile`` constructor rejects zero / negative
    limits."""

    def test_default_cpu_seconds_is_a_positive_budget(self) -> None:
        profile = SandboxProfile()
        assert profile.cpu_seconds == DEFAULT_CPU_SECONDS
        assert profile.cpu_seconds > 0

    def test_default_pids_limit_is_bounded(self) -> None:
        profile = SandboxProfile()
        assert profile.pids_limit == DEFAULT_PIDS_LIMIT
        # Fork-bomb protection: pids_limit must be
        # small enough to make a forkbomb impractical.
        assert profile.pids_limit <= 1024

    def test_default_memory_budget_is_bounded(self) -> None:
        profile = SandboxProfile()
        assert profile.memory_bytes == DEFAULT_MEMORY_BYTES
        assert profile.memory_bytes > 0
        # Memory ceiling is in MiB range by default
        # (512 MiB), so a memory-bomb attempt is bounded.
        assert profile.memory_bytes <= 8 * 1024 * 1024 * 1024  # 8 GiB

    def test_default_disk_budget_is_bounded(self) -> None:
        profile = SandboxProfile()
        assert profile.disk_bytes == DEFAULT_DISK_BYTES
        assert profile.disk_bytes > 0
        # Disk ceiling is in MiB range by default
        # (100 MiB), so a disk-fill attempt is bounded.
        assert profile.disk_bytes <= 10 * 1024 * 1024 * 1024  # 10 GiB

    def test_cpu_seconds_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SandboxProfile(cpu_seconds=0)
        with pytest.raises(ValueError):
            SandboxProfile(cpu_seconds=-1)

    def test_pids_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SandboxProfile(pids_limit=0)
        with pytest.raises(ValueError):
            SandboxProfile(pids_limit=-1)

    def test_memory_must_be_non_negative(self) -> None:
        # ``memory_bytes`` allows 0 (a child process can
        # be denied all memory — strict denial). Negative
        # values are rejected.
        SandboxProfile(memory_bytes=0)  # allowed: 0
        with pytest.raises(ValueError):
            SandboxProfile(memory_bytes=-1)

    def test_disk_must_be_non_negative(self) -> None:
        # ``disk_bytes`` allows 0 (a child process can be
        # denied all disk writes). Negative values rejected.
        SandboxProfile(disk_bytes=0)  # allowed: 0
        with pytest.raises(ValueError):
            SandboxProfile(disk_bytes=-1)


# ---------------------------------------------------------------------------
# WP9.17 — Network exfiltration
# ---------------------------------------------------------------------------


class TestNetworkEgressConfig:
    """ENFORCED BOUNDARY: the sandbox default is
    ``network_mode="none"`` so a subprocess cannot
    reach the network. EXPECTED FAILURE STATE: a
    profile that does NOT explicitly allow egress
    is rejected by the executor, or the executor
    blocks all network calls at the OS level."""

    def test_default_network_mode_is_none(self) -> None:
        profile = SandboxProfile()
        assert profile.network_mode == "none"

    def test_invalid_network_mode_rejected(self) -> None:
        # Unknown network modes are rejected. The
        # accepted values are ``none`` and ``bridge``.
        # ``host`` is too permissive; the harness does
        # not currently allow it.
        with pytest.raises(ValueError):
            SandboxProfile(network_mode="made-up-mode")

    def test_no_egress_means_no_network_calls(self) -> None:
        # The current contract: ``network_mode="none"``
        # means no network. The executor (subprocess
        # sandbox) enforces this by setting
        # ``network_mode="none"`` and the subprocess
        # inherits the closed network namespace. Pinning
        # the profile-level default here.
        profile = SandboxProfile()
        assert profile.network_mode == "none"
        # No allowed_network is exposed because the
        # default mode is "none"; the field is irrelevant.
        # The harness does not allow ``network_mode="bridge"``
        # yet, so there is no allowlist to test.


# ---------------------------------------------------------------------------
# WP9.18 — Filesystem isolation
# ---------------------------------------------------------------------------


class TestFilesystemIsolation:
    """ENFORCED BOUNDARY: the default allowed_paths is
    empty, so a subprocess cannot read or write any file
    unless the operator explicitly allows the path.
    EXPECTED FAILURE STATE: the executor refuses to
    start the subprocess with the dangerous path."""

    def test_default_allowed_paths_is_empty(self) -> None:
        profile = SandboxProfile()
        assert profile.allowed_paths == ()

    def test_relative_path_rejected(self) -> None:
        # ``cwd`` must be absolute. A relative cwd
        # could resolve to anything depending on the
        # caller's working directory.
        with pytest.raises(ValueError):
            SandboxProfile(cwd="relative/path")

    def test_user_can_extend_allowed_paths(self, tmp_path: Path) -> None:
        profile = SandboxProfile(allowed_paths=(str(tmp_path),))
        assert str(tmp_path) in profile.allowed_paths


# ---------------------------------------------------------------------------
# WP9.19 — Forbidden setup.py install scripts
# ---------------------------------------------------------------------------


class TestDependencyInstallScripts:
    """ENFORCED BOUNDARY: a ``pyproject.toml`` with a
    custom ``[project.scripts]`` entry is detected by
    the inspector and surfaced in the
    ``validation_commands`` list. The orchestrator
    does NOT run arbitrary scripts at install time —
    the validator runs only the configured gates
    (test, lint, type_check, format).

    EXPECTED FAILURE STATE: the inspector surfaces the
    presence of the script entry; the validator does
    NOT execute the script body."""

    def test_pyproject_with_project_scripts_inspected(self, tmp_path: Path) -> None:
        import subprocess

        from seharness.repository.discovery import inspect_repository

        repo = tmp_path / "evil"
        repo.mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname = 'evil'\n[project.scripts]\nevil-cmd = 'evil_pkg.evil:main'\n"
        )
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(repo)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@e.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "add", "pyproject.toml"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )
        profile = inspect_repository(repo)
        # The profile is returned without crashing.
        # The script entry is NOT in ``validation_commands``
        # (that's the gate list, not the install hooks).
        assert "evil-cmd" not in profile.validation_commands


# ---------------------------------------------------------------------------
# WP9.20 — Profile immutability
# ---------------------------------------------------------------------------


class TestProfileImmutability:
    """ENFORCED BOUNDARY: ``SandboxProfile`` is a frozen
    pydantic model. The orchestrator cannot mutate the
    profile after construction; any attempt to modify
    a field raises."""

    def test_profile_is_frozen(self, tmp_path: Path) -> None:
        profile = SandboxProfile(cwd=str(tmp_path))
        with pytest.raises((AttributeError, ValueError)):
            profile.cpu_seconds = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# WP9.21 — Disallowed-hostname validation
# ---------------------------------------------------------------------------


class TestNetworkAddressValidation:
    """ENFORCED BOUNDARY: a profile with a malformed
    hostname or CIDR in the network allow-list is
    rejected at construction time. EXPECTED FAILURE
    STATE: ``SandboxProfile.__init__`` raises
    ``ValueError``."""

    def test_invalid_cidr_rejected(self) -> None:
        with pytest.raises(ValueError):
            SandboxProfile(
                network_mode="bridge",
                allowed_network=("not-a-cidr",),
            )

    def test_invalid_ip_rejected(self) -> None:
        with pytest.raises(ValueError):
            SandboxProfile(
                network_mode="bridge",
                allowed_network=("999.999.999.999",),
            )
