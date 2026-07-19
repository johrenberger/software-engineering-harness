"""Tests for ``DockerSandbox.run`` paths that can be exercised without
a live Docker daemon — we monkeypatch the underlying ``_docker_client``
to return a fake client and assert the orchestrator surfaces success
and failure correctly.

These tests directly execute lines 79-106 of
``src/seharness/sandbox/docker.py`` (the success path and the
``DockerException`` handling path), which were previously uncovered
without a Docker daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeContainerOutput:
    output: bytes

    def decode(self, encoding: str, errors: str = "strict") -> str:
        return self.output.decode(encoding, errors=errors)


@dataclass
class _FakeException:
    message: str


class _FakeContainers:
    """In-memory ``client.containers`` replacement."""

    def __init__(self, *, raises: Exception | None = None, output: bytes = b"") -> None:
        self.raises = raises
        self.output = output
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> bytes:
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.output


class _FakeClient:
    """In-memory ``docker.from_env()`` replacement."""

    def __init__(self, *, raises: Exception | None = None, output: bytes = b"") -> None:
        self._containers = _FakeContainers(raises=raises, output=output)
        self.ping_count = 0

    def ping(self) -> None:
        self.ping_count += 1

    @property
    def containers(self) -> _FakeContainers:
        return self._containers


# ---------------------------------------------------------------------------
# DockerSandbox.run success path
# ---------------------------------------------------------------------------


class TestDockerSandboxRunSuccess:
    """Lines 79-106: the success path inside ``DockerSandbox.run``."""

    def test_run_returns_sandbox_result_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from docker.errors import DockerException  # noqa: PLC0415

        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415

        client = _FakeClient(output=b"hello-from-container")
        monkeypatch.setattr(
            "seharness.sandbox.docker._docker_client",
            lambda: client,
        )

        sandbox = DockerSandbox()
        profile = SandboxProfile(
            cwd=str(tmp_path),
            cpu_seconds=2.0,
            allowed_paths=(str(tmp_path),),
        )
        result = sandbox.run("printf hello-from-container", profile=profile)
        assert result.exit_code == 0
        assert "hello-from-container" in result.stdout
        assert result.sandbox_violations == ()
        # _FakeClient.ping() may or may not have been called depending on
        # whether the docker SDK pings again before run; we only assert
        # the fake ran the call we configured.
        assert client.containers.calls
        # The kwargs we passed to containers.run captured the profile.
        kw = client.containers.calls[0]
        assert kw["image"] == "python:3.13-slim"
        assert kw["command"] == ["/bin/sh", "-c", "printf hello-from-container"]
        # Volume mount for the cwd is present.
        volumes = kw["volumes"]
        if isinstance(volumes, dict):
            assert str(tmp_path) in volumes
        del DockerException  # silence unused-import lint


class TestDockerSandboxRunFailure:
    """Lines 79-102: ``DockerException`` handling inside ``DockerSandbox.run``."""

    def test_run_returns_error_result_when_container_run_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from docker.errors import DockerException  # noqa: PLC0415

        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415

        client = _FakeClient(raises=DockerException("container exploded"))
        monkeypatch.setattr(
            "seharness.sandbox.docker._docker_client",
            lambda: client,
        )

        sandbox = DockerSandbox()
        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=2.0)
        result = sandbox.run("false", profile=profile)
        assert result.exit_code == -1
        assert "docker run failed" in result.stderr
        assert "container exploded" in result.stderr
        assert result.sandbox_violations == ("docker_run_failed",)


class TestDockerSandboxRunDockerUnavailable:
    """Lines 79-106: ``DockerUnavailable`` short-circuit."""

    def test_run_returns_error_when_daemon_unreachable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from seharness.sandbox import DockerSandbox, SandboxProfile  # noqa: PLC0415
        from seharness.sandbox.docker import DockerUnavailable  # noqa: PLC0415

        def _raise() -> None:
            raise DockerUnavailable("fake: daemon gone")

        monkeypatch.setattr(
            "seharness.sandbox.docker._docker_client",
            _raise,
        )

        sandbox = DockerSandbox()
        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=2.0)
        result = sandbox.run("echo hi", profile=profile)
        assert result.exit_code == -1
        assert "docker unavailable" in result.stderr
        assert result.sandbox_violations == ("docker_unavailable",)
