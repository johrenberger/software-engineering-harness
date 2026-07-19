"""RED-style fail-closed tests for Cluster C, story C5.

These tests validate the *configuration* and *wiring* of the sandbox
isolation primitives without executing real attack payloads. We do
NOT spawn forkbombs, real outbound network calls, real long-running
sleeps, or real curl probes here - those would risk OOM-killing the
host on which the test suite runs (which has happened during
Cluster C development).

The harness's isolation guarantees are validated at three layers:

1. **Profile parsing** - :class:`SandboxProfile` rejects unsafe
   configurations at construction time (CPU/memory/FSIZE/NOFILE/
   PROC bounds present, default denials applied).
2. **API wiring** - every executor implements the
   :class:`SandboxExecutor` protocol and accepts the documented
   arguments.
3. **Tighter smoke tests** - a single tiny ``echo`` invocation to
   verify each executor actually launches a child process and
   surfaces results. Bounded to < 1s wall-clock each.

Threat-model coverage of *real* red-team payloads (symlink
traversal, DNS exfiltration, real forkbomb, real curl) is delegated
to a future CI matrix that runs inside disposable containers.
Recorded as a follow-up issue in the cluster evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

# ---------------------------------------------------------------------------
# 1. Profile parsing - fail-closed at construction time
# ---------------------------------------------------------------------------


class TestProfileFailClosed:
    """``SandboxProfile`` records every isolation bound so the executor
    can enforce it. The values, not their enforcement, are validated
    here."""

    def test_default_cwd_is_current_dir(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.cwd  # non-empty

    def test_default_cpu_seconds_is_a_positive_budget(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.cpu_seconds > 0.0
        assert profile.cpu_seconds <= 3600.0  # 1h hard ceiling

    def test_default_pids_limit_is_bounded(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.pids_limit >= 1
        # Cap so a runaway child cannot exhaust the host PID table.
        assert profile.pids_limit <= 4096

    def test_default_memory_budget_is_a_positive_bounded_value(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.memory_bytes > 0
        assert profile.memory_bytes <= 8 * 1024**3  # 8GiB hard ceiling

    def test_default_disk_budget_is_a_positive_bounded_value(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.disk_bytes > 0
        assert profile.disk_bytes <= 100 * 1024**3  # 100GiB hard ceiling

    def test_default_network_egress_is_blocked(self) -> None:
        """A fail-closed default lists no allowed destinations - any
        egress is denied unless an operator explicitly allowlists it."""
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.allowed_network_destinations == ()

    def test_default_filesystem_is_allowlist_only(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        assert profile.allowed_paths == ()

    def test_default_denies_canonical_secret_env_vars(self) -> None:
        """Defaults must scrub the canonical secret-bearing env var
        names so a child inherits nothing dangerous by accident."""
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile()
        denied = set(profile.denied_env_vars)
        for token in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GITLAB_TOKEN",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            assert token in denied, f"missing default deny for {token}"

    def test_user_can_extend_default_deny_list(self) -> None:
        """User-supplied denied env vars are merged with the
        built-in defaults, never replacing them."""
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(denied_env_vars=("SLACK_BOT_TOKEN",))
        denied = set(profile.denied_env_vars)
        # Built-in default preserved.
        assert "AWS_ACCESS_KEY_ID" in denied
        # User entry appended.
        assert "SLACK_BOT_TOKEN" in denied

    def test_cpu_seconds_must_be_positive(self, tmp_path: Path) -> None:
        from pydantic import ValidationError  # noqa: PLC0415

        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(cwd=str(tmp_path), cpu_seconds=0.0)
        with pytest.raises(ValidationError):
            SandboxProfile(cwd=str(tmp_path), cpu_seconds=-1.0)

    def test_pids_limit_must_be_positive(self, tmp_path: Path) -> None:
        from pydantic import ValidationError  # noqa: PLC0415

        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        with pytest.raises(ValidationError):
            SandboxProfile(cwd=str(tmp_path), pids_limit=0)
        with pytest.raises(ValidationError):
            SandboxProfile(cwd=str(tmp_path), pids_limit=-1)

    def test_profile_is_immutable(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        with pytest.raises((AttributeError, TypeError, ValueError)):
            profile.cpu_seconds = 999.0  # type: ignore[misc]

    def test_profile_can_carry_explicit_allowlists(self) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(
            allowed_paths=("/repo",),
            allowed_network_destinations=("pypi.org",),
            denied_env_vars=("MY_SECRET",),
        )
        assert profile.allowed_paths == ("/repo",)
        assert profile.allowed_network_destinations == ("pypi.org",)
        assert "MY_SECRET" in profile.denied_env_vars


# ---------------------------------------------------------------------------
# 2. API wiring - every executor implements SandboxExecutor
# ---------------------------------------------------------------------------


@runtime_checkable
class _HasRun(Protocol):
    """Minimum surface every executor must expose."""

    def run(
        self,
        command: str,
        *,
        profile: object,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
    ) -> object: ...


class TestExecutorAPIWiring:
    """All three executors implement :class:`SandboxExecutor` and
    accept the documented constructor."""

    def test_noop_sandbox_implements_protocol(self) -> None:
        from seharness.sandbox import NoopSandbox, SandboxExecutor  # noqa: PLC0415

        executor = NoopSandbox()
        assert isinstance(executor, SandboxExecutor)

    def test_subprocess_sandbox_implements_protocol(self) -> None:
        from seharness.sandbox import SandboxExecutor, SubprocessSandbox  # noqa: PLC0415

        executor = SubprocessSandbox()
        assert isinstance(executor, SandboxExecutor)

    def test_docker_sandbox_implements_protocol(self) -> None:
        from seharness.sandbox import DockerSandbox, SandboxExecutor  # noqa: PLC0415

        executor = DockerSandbox()
        assert isinstance(executor, SandboxExecutor)

    def test_all_executors_have_run_method(self) -> None:
        from seharness.sandbox import (  # noqa: PLC0415
            DockerSandbox,
            NoopSandbox,
            SubprocessSandbox,
        )

        for cls in (NoopSandbox, SubprocessSandbox, DockerSandbox):
            assert hasattr(cls, "run")
            assert callable(cls.run)

    def test_subprocess_sandbox_does_not_override_noop(self) -> None:
        """The SubprocessSandbox must not be a NoopSandbox - it must
        actually apply constraints."""
        from seharness.sandbox import NoopSandbox, SubprocessSandbox  # noqa: PLC0415

        assert not issubclass(SubprocessSandbox, NoopSandbox)
        assert not issubclass(NoopSandbox, SubprocessSandbox)


# ---------------------------------------------------------------------------
# 3. Tight execution smoke tests - single bounded ``echo`` per executor
# ---------------------------------------------------------------------------


class TestEchoSmoke:
    """One quick ``echo`` per executor - verifies the executor can
    actually launch a child and surface a :class:`SandboxResult`.
    Wall-clock budget is 2s per executor."""

    def test_noop_sandbox_echo(self, tmp_path: Path) -> None:
        from seharness.sandbox import NoopSandbox, SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=2.0)
        result = NoopSandbox().run("echo c5-noop", profile=profile)
        assert result.exit_code == 0
        assert "c5-noop" in result.stdout
        assert result.duration_s >= 0.0

    def test_subprocess_sandbox_echo(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=2.0)
        result = SubprocessSandbox().run("echo c5-sub", profile=profile)
        assert result.exit_code == 0
        assert "c5-sub" in result.stdout
        assert result.duration_s >= 0.0

    def test_default_executor_in_task_execution_service_is_noop(
        self, tmp_path: Path
    ) -> None:
        """Story C4 specifies that without a configured sandbox,
        TaskExecutionService behaves as before (NoopSandbox default)."""
        from seharness.execution.service import TaskExecutionService  # noqa: PLC0415

        service = TaskExecutionService(
            repo_root=tmp_path,
            execution_root=tmp_path / "exec",
        )
        from seharness.sandbox import NoopSandbox  # noqa: PLC0415

        assert isinstance(service.sandbox, NoopSandbox)

    def test_default_executor_in_subprocess_runner_is_noop(
        self, tmp_path: Path
    ) -> None:
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        runner = SubprocessRunner()
        from seharness.sandbox import NoopSandbox  # noqa: PLC0415

        assert isinstance(runner._sandbox, NoopSandbox)


# ---------------------------------------------------------------------------
# 4. Fail-closed config combinations
# ---------------------------------------------------------------------------


class TestConfigCombinations:
    """Configuration combinations the executor must accept or reject."""

    def test_empty_allowed_network_blocks_docker_egress_intent(
        self, tmp_path: Path
    ) -> None:
        """Empty network allowlist is the fail-closed default."""
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(
            cwd=str(tmp_path),
            allowed_network_destinations=(),
        )
        assert profile.allowed_network_destinations == ()

    def test_empty_allowed_paths_intent(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(
            cwd=str(tmp_path),
            allowed_paths=(),
        )
        assert profile.allowed_paths == ()

    def test_cpu_bound_is_recorded_in_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=2.5)
        assert profile.cpu_seconds == 2.5

    def test_pids_limit_is_recorded_in_profile(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), pids_limit=64)
        assert profile.pids_limit == 64
