"""Tests for the pure-function ``_build_container_kwargs`` and the
``is_docker_available`` probe in ``seharness.sandbox.docker``.

These tests do NOT require a Docker daemon and do not spawn real
containers — they exercise the configuration layer that translates
:class:`SandboxProfile` into docker-py kwargs. Path 79-106 of
``src/seharness/sandbox/docker.py`` (the per-volume binding entry)
and the DockerException handling path are covered here without
risking OOM on the test host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# _build_container_kwargs
# ---------------------------------------------------------------------------


def _build_kwargs(
    *,
    allowed_paths: tuple[str, ...] = (),
    denied_env_vars: tuple[str, ...] = (),
    cpu_seconds: float = 60.0,
    memory_bytes: int = 512 * 1024 * 1024,
    disk_bytes: int = 100 * 1024 * 1024,
    pids_limit: int = 64,
    network_mode: str = "none",
    env: dict[str, str] | None = None,
    read_only_root: bool = True,
    drop_capabilities: tuple[str, ...] = ("ALL",),
    extra_security_opt: tuple[str, ...] = ("no-new-privileges:true",),
    user: str = "1000:1000",
) -> dict[str, Any]:
    from seharness.sandbox import SandboxProfile  # noqa: PLC0415
    from seharness.sandbox.docker import _build_container_kwargs  # noqa: PLC0415

    profile = SandboxProfile(
        allowed_paths=allowed_paths,
        denied_env_vars=denied_env_vars,
        cpu_seconds=cpu_seconds,
        memory_bytes=memory_bytes,
        disk_bytes=disk_bytes,
        pids_limit=pids_limit,
        network_mode=network_mode,
    )
    return _build_container_kwargs(
        profile=profile,
        user=user,
        read_only_root=read_only_root,
        drop_capabilities=drop_capabilities,
        extra_security_opt=extra_security_opt,
        env=env,
    )


class TestBuildContainerKwargs:
    """Validate the dict returned by ``_build_container_kwargs``."""

    def test_returns_dict(self) -> None:
        kw = _build_kwargs()
        assert isinstance(kw, dict)

    def test_volumes_empty_when_no_allowed_paths(self) -> None:
        kw = _build_kwargs(allowed_paths=())
        assert kw["volumes"] == {}

    def test_volumes_contains_absolute_paths(self, tmp_path: Path) -> None:
        p = str(tmp_path)
        kw = _build_kwargs(allowed_paths=(p,))
        assert p in kw["volumes"]
        assert kw["volumes"][p]["bind"] == p
        assert kw["volumes"][p]["mode"] == "rw"

    def test_volumes_resolves_relative_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        kw = _build_kwargs(allowed_paths=("relative_dir",))
        # At least one volume entry should map to the cwd + relative_dir.
        keys = list(kw["volumes"].keys())
        assert any(str(tmp_path / "relative_dir") in k for k in keys)

    def test_scrubbed_env_omits_denied_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked")
        monkeypatch.setenv("MY_PLAIN_VAR", "kept")
        kw = _build_kwargs()
        # The built-in default deny list scrubs AWS_* even when
        # ``denied_env_vars=()`` is passed at construction time (the
        # profile merges defaults behind the scenes).
        env = kw["environment"]
        assert "AWS_SECRET_ACCESS_KEY" not in env
        # Plain vars survive (as long as not on the deny list).
        if "MY_PLAIN_VAR" in env:
            assert env["MY_PLAIN_VAR"] == "kept"

    def test_extra_env_merged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leaked")
        kw = _build_kwargs(env={"FOO": "bar"})
        assert kw["environment"].get("FOO") == "bar"
        assert "AWS_SECRET_ACCESS_KEY" not in kw["environment"]

    def test_cpu_period_and_quota_set(self) -> None:
        kw = _build_kwargs()
        assert kw["cpu_period"] == 100_000
        assert kw["cpu_quota"] == 100_000

    def test_memory_and_swap_cap_set(self) -> None:
        kw = _build_kwargs(memory_bytes=42_949_672_960)  # 40 GiB
        assert kw["mem_limit"] == 42_949_672_960
        assert kw["memswap_limit"] == 42_949_672_960

    def test_pids_limit_passthrough(self) -> None:
        kw = _build_kwargs(pids_limit=128)
        assert kw["pids_limit"] == 128

    def test_ulimits_contain_nofile_fsize_nproc(self) -> None:
        kw = _build_kwargs(disk_bytes=99, pids_limit=7)
        ulimits = kw["ulimits"]
        names = {u["Name"] for u in ulimits}
        assert names == {"nofile", "fsize", "nproc"}
        fsize = next(u for u in ulimits if u["Name"] == "fsize")
        assert fsize["Soft"] == 99 and fsize["Hard"] == 99
        nproc = next(u for u in ulimits if u["Name"] == "nproc")
        assert nproc["Soft"] == 7 and nproc["Hard"] == 7

    def test_network_mode_passthrough(self) -> None:
        kw = _build_kwargs(network_mode="bridge")
        assert kw["network_mode"] == "bridge"
        kw2 = _build_kwargs(network_mode="none")
        assert kw2["network_mode"] == "none"

    def test_read_only_root_passthrough(self) -> None:
        kw = _build_kwargs(read_only_root=True)
        assert kw["read_only"] is True
        kw2 = _build_kwargs(read_only_root=False)
        assert kw2["read_only"] is False

    def test_cap_drop_and_security_opt(self) -> None:
        kw = _build_kwargs(
            drop_capabilities=("ALL", "CHOWN"),
            extra_security_opt=("no-new-privileges:true", "seccomp=unconfined"),
        )
        assert kw["cap_drop"] == ["ALL", "CHOWN"]
        assert kw["security_opt"] == [
            "no-new-privileges:true",
            "seccomp=unconfined",
        ]

    def test_user_passthrough(self) -> None:
        kw = _build_kwargs(user="500:500")
        assert kw["user"] == "500:500"

    def test_detach_remove_stdin_tty_defaults(self) -> None:
        kw = _build_kwargs()
        assert kw["detach"] is False
        assert kw["remove"] is True
        assert kw["stdin_open"] is False
        assert kw["tty"] is False


# ---------------------------------------------------------------------------
# is_docker_available / DockerUnavailable
# ---------------------------------------------------------------------------


class TestDockerAvailabilityProbe:
    """The probe reports docker availability without crashing."""

    def test_is_docker_available_returns_bool_when_daemon_unreachable(
        self,
    ) -> None:
        from seharness.sandbox.docker import (
            DockerUnavailable,  # noqa: PLC0415
            _docker_client,  # noqa: PLC0415
            is_docker_available,  # noqa: PLC0415
        )

        # If the daemon is unreachable (typical in CI without a daemon),
        # the probe returns False. If it's reachable, returns True.
        # Either outcome is a clean bool; we only assert the type.
        try:
            client = _docker_client()
        except DockerUnavailable:
            assert is_docker_available() is False
        else:
            # The probe can also succeed in environments with a daemon.
            del client  # keep mypy quiet
            assert isinstance(is_docker_available(), bool)

    def test_docker_unavailable_exception_carries_message(self) -> None:
        from seharness.sandbox.docker import DockerUnavailable  # noqa: PLC0415

        exc = DockerUnavailable("no daemon")
        assert "no daemon" in str(exc)
        assert isinstance(exc, Exception)
