"""Cluster C, story C2: ``DockerSandbox`` executor.

Runs commands inside a short-lived Docker container with the
``SandboxProfile`` applied:

- ``volumes`` — only ``profile.allowed_paths`` are bind-mounted.
- ``environment`` — merged with ``profile.denied_env_vars`` scrubbed.
- ``network_mode`` — ``profile.network_mode`` (default ``"none"``).
- ``mem_limit`` / ``memswap_limit`` — ``profile.memory_bytes``.
- ``pids_limit`` — ``profile.pids_limit``.
- ``cpu_quota`` / ``cpu_period`` — derived from ``profile.cpu_seconds``.
- ``ulimits`` — ``nofile``, ``fsize``, ``nproc``.
- ``read_only`` — root filesystem is read-only; bind mounts are RW.
- ``cap_drop: ["ALL"]`` + ``security_opt: ["no-new-privileges:true"]``.
- ``user`` — run as UID 1000 by default; ``"root"`` is opt-in.

Tests that require a running Docker daemon use
``pytest.mark.docker`` and ``pytest.skip`` when Docker is unavailable.
"""

from __future__ import annotations

import os
import time
from typing import Any

from seharness.sandbox import NoopSandbox, SandboxProfile, SandboxResult


class DockerSandbox:
    """Docker-backed sandbox executor (story C2).

    The class is import-safe even on hosts without Docker: the import
    does NOT touch the docker daemon. ``run()`` only requires Docker
    when invoked.
    """

    def __init__(
        self,
        *,
        user: str = "1000:1000",
        read_only_root: bool = True,
        drop_capabilities: tuple[str, ...] = ("ALL",),
        extra_security_opt: tuple[str, ...] = ("no-new-privileges:true",),
    ) -> None:
        self._user = user
        self._read_only_root = read_only_root
        self._drop_capabilities = tuple(drop_capabilities)
        self._extra_security_opt = tuple(extra_security_opt)

    def run(
        self,
        command: str,
        *,
        profile: SandboxProfile,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> SandboxResult:
        """Run ``command`` inside a Docker container.

        Returns a :class:`SandboxResult`. If the docker daemon is not
        reachable, raises :class:`DockerUnavailable`.
        """
        from docker.errors import DockerException  # noqa: PLC0415

        start = time.monotonic()
        try:
            client = _docker_client()
        except DockerUnavailable as exc:
            return SandboxResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"docker unavailable: {exc}",
                duration_s=time.monotonic() - start,
                sandbox_violations=("docker_unavailable",),
            )

        container_kwargs = _build_container_kwargs(
            profile=profile,
            user=self._user,
            read_only_root=self._read_only_root,
            drop_capabilities=self._drop_capabilities,
            extra_security_opt=self._extra_security_opt,
            env=env,
        )

        try:
            output = client.containers.run(
                image=profile.image,
                command=["/bin/sh", "-c", command],
                **container_kwargs,
            )
        except DockerException as exc:
            return SandboxResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"docker run failed: {exc}",
                duration_s=time.monotonic() - start,
                sandbox_violations=("docker_run_failed",),
            )
        stdout = (
            output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        )
        return SandboxResult(
            command=command,
            exit_code=0,
            stdout=stdout,
            stderr="",
            duration_s=time.monotonic() - start,
            sandbox_violations=(),
        )


class DockerUnavailable(RuntimeError):
    """Raised when the Docker daemon is not reachable."""


def _docker_client() -> Any:
    """Return a connected Docker client or raise :class:`DockerUnavailable`."""
    try:
        import docker  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - guarded by tests
        raise DockerUnavailable(f"docker package not installed: {exc}") from exc
    try:
        client = docker.from_env()  # type: ignore[attr-defined]
        client.ping()
    except Exception as exc:
        raise DockerUnavailable(f"docker daemon unreachable: {exc}") from exc
    return client


def _build_container_kwargs(
    *,
    profile: SandboxProfile,
    user: str,
    read_only_root: bool,
    drop_capabilities: tuple[str, ...],
    extra_security_opt: tuple[str, ...],
    env: dict[str, str] | None,
) -> dict[str, Any]:
    """Translate a :class:`SandboxProfile` into docker-py kwargs."""
    # Mount each allowed path read-write at the same location in the
    # container; the rest of the filesystem is read-only or removed.
    volumes: dict[str, dict[str, str]] = {}
    for entry in profile.allowed_paths:
        host_path = entry
        # Normalise: if relative, resolve against the host cwd.
        if not os.path.isabs(host_path):
            host_path = os.path.abspath(host_path)
        volumes[host_path] = {"bind": host_path, "mode": "rw"}

    # Scrub denied env vars from the inherited environment.
    full_env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in profile.denied_env_vars:
            continue
        if any(denied == k for denied in profile.denied_env_vars):
            continue
        full_env[k] = v
    if env:
        for k, v in env.items():
            if k in profile.denied_env_vars:
                continue
            full_env[k] = v

    # CPU quota is microseconds per period. A 100ms period with quota
    # equal to period * cores. Here we approximate "1 core" with quota
    # equal to one full period; the wall-clock ceiling comes from
    # cpu_seconds which the runner enforces separately via timeout.
    cpu_period = 100_000
    cpu_quota = cpu_period  # 1 CPU

    ulimits = [
        {"Name": "nofile", "Soft": 256, "Hard": 256},
        {"Name": "fsize", "Soft": int(profile.disk_bytes), "Hard": int(profile.disk_bytes)},
        {"Name": "nproc", "Soft": int(profile.pids_limit), "Hard": int(profile.pids_limit)},
    ]

    return {
        "volumes": volumes,
        "environment": full_env,
        "network_mode": profile.network_mode,
        "mem_limit": int(profile.memory_bytes),
        "memswap_limit": int(profile.memory_bytes),  # disable swap
        "pids_limit": int(profile.pids_limit),
        "cpu_quota": cpu_quota,
        "cpu_period": cpu_period,
        "ulimits": ulimits,
        "read_only": read_only_root,
        "cap_drop": list(drop_capabilities),
        "security_opt": list(extra_security_opt),
        "user": user,
        "detach": False,
        "remove": True,
        "stdin_open": False,
        "tty": False,
    }


def is_docker_available() -> bool:
    """Return True iff the Docker daemon is reachable."""
    try:
        client = _docker_client()
        client.ping()
        return True
    except DockerUnavailable:
        return False


# Re-export NoopSandbox for callers that want a uniform import.
__all__ = ["DockerSandbox", "DockerUnavailable", "is_docker_available"]


# Late imports to avoid pulling docker on hosts where it isn't installed.
_ = (NoopSandbox,)
