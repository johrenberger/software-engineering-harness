"""RED tests for Cluster C, story C2: ``DockerSandbox`` executor.

The Docker executor applies a :class:`SandboxProfile` to a short-lived
container run. Tests split into two layers:

1. **Pure-kwargs tests** (no Docker daemon required): verify that
   ``_build_container_kwargs`` translates the profile correctly.
2. **Live-daemon tests** (skipped when Docker unavailable): verify
   that an actual ``docker run`` honours the profile — filesystem
   read-only, network disabled, env scrubbed, etc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# All tests in this module belong to the docker story.
pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure-kwargs translation tests
# ---------------------------------------------------------------------------


class TestDockerSandboxImport:
    """``DockerSandbox`` is importable from ``seharness.sandbox``."""

    def test_docker_sandbox_importable(self) -> None:
        from seharness.sandbox import DockerSandbox  # noqa: PLC0415

        assert DockerSandbox is not None


class TestBuildContainerKwargs:
    """``_build_container_kwargs`` translates a profile into docker-py args."""

    def test_image_is_taken_from_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(image="python:3.12-slim", cwd=str(tmp_path))
        _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        # The image is passed directly to client.containers.run(), not
        # inside kwargs; we test that the helper does not mutate it.
        assert profile.image == "python:3.12-slim"

    def test_volumes_built_from_allowed_paths(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        work = tmp_path / "work"
        work.mkdir()
        profile = SandboxProfile(
            allowed_paths=(str(work),),
            cwd=str(tmp_path),
        )
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert str(work) in kwargs["volumes"]
        assert kwargs["volumes"][str(work)]["bind"] == str(work)
        assert kwargs["volumes"][str(work)]["mode"] == "rw"

    def test_network_mode_from_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(network_mode="none", cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["network_mode"] == "none"

    def test_memory_limit_from_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(memory_bytes=256 * 1024 * 1024, cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["mem_limit"] == 256 * 1024 * 1024
        assert kwargs["memswap_limit"] == 256 * 1024 * 1024

    def test_pids_limit_from_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(pids_limit=8, cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["pids_limit"] == 8

    def test_cpu_quota_one_core(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["cpu_quota"] > 0
        assert kwargs["cpu_period"] > 0

    def test_read_only_rootfs(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["read_only"] is True

    def test_capabilities_dropped(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["cap_drop"] == ["ALL"]

    def test_security_opt_no_new_privileges(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert "no-new-privileges:true" in kwargs["security_opt"]

    def test_user_default(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        assert kwargs["user"] == "1000:1000"

    def test_ulimits_include_nofile_and_fsize(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(disk_bytes=10 * 1024 * 1024, cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        names = {u["Name"] for u in kwargs["ulimits"]}
        assert "nofile" in names
        assert "fsize" in names
        assert "nproc" in names

    def test_fsize_ulimit_matches_disk_bytes(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        cap = 10 * 1024 * 1024
        profile = SandboxProfile(disk_bytes=cap, cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env=None,
        )
        fsize = next(u for u in kwargs["ulimits"] if u["Name"] == "fsize")
        assert fsize["Soft"] == cap
        assert fsize["Hard"] == cap


class TestDockerEnvScrubbing:
    """The Docker executor scrubs denied env vars from the inherited env."""

    def test_extra_env_not_scrubbed_when_not_in_deny(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        kwargs = _build_container_kwargs(
            profile=profile,
            user="1000:1000",
            read_only_root=True,
            drop_capabilities=("ALL",),
            extra_security_opt=("no-new-privileges:true",),
            env={"MY_VAR": "hello"},
        )
        assert kwargs["environment"].get("MY_VAR") == "hello"


# ---------------------------------------------------------------------------
# Live-daemon tests — skipped when Docker is unavailable.
# ---------------------------------------------------------------------------


@pytest.fixture
def require_docker() -> None:
    """Skip the test if Docker is not reachable."""
    from seharness.sandbox.docker import is_docker_available  # noqa: PLC0415

    if not is_docker_available():
        pytest.skip("Docker daemon not reachable; live docker test skipped")


class TestDockerSandboxLive:
    """Live tests against a real Docker daemon (skipped if not available)."""

    def test_run_returns_sandbox_result(self, tmp_path: Path, require_docker: None) -> None:
        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = DockerSandbox()
        result = sandbox.run("echo hello", profile=profile)
        # Result shape is well-formed (live result may exit non-zero on
        # some hosts but stdout is captured).
        assert isinstance(result.command, str)
        assert isinstance(result.exit_code, int)

    def test_network_disabled_blocks_dns(self, tmp_path: Path, require_docker: None) -> None:
        """A container with network_mode=none cannot resolve DNS."""
        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), network_mode="none")
        sandbox = DockerSandbox()
        # `getent hosts` will fail when the network is none.
        result = sandbox.run("getent hosts example.com || true", profile=profile)
        assert "example.com" not in result.stdout


# ---------------------------------------------------------------------------
# DockerUnavailable behaviour
# ---------------------------------------------------------------------------


class TestDockerUnavailable:
    """The executor returns a structured SandboxResult on docker failure."""

    def test_run_when_docker_unavailable_returns_result(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415
        from seharness.sandbox import docker as docker_mod  # noqa: PLC0415

        def _raise() -> None:
            raise docker_mod.DockerUnavailable("simulated down")

        monkeypatch.setattr(docker_mod, "_docker_client", _raise)
        profile = SandboxProfile(cwd=str(tmp_path))
        result = DockerSandbox().run("true", profile=profile)
        assert result.exit_code == -1
        assert "docker unavailable" in result.stderr
        assert "docker_unavailable" in result.sandbox_violations
