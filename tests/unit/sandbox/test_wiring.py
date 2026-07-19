"""RED tests for Cluster C, story C4: wire SandboxProfile into
``TaskExecutionService`` and ``SubprocessRunner``.

After C4, callers that opt into a ``SandboxExecutor`` get profile-driven
isolation for every subprocess the harness spawns. Existing callers
(those that don't pass a sandbox) continue to work unchanged via the
``NoopSandbox`` default.

The wiring points are:

- :class:`TaskExecutionService` — accepts an optional ``sandbox``
  constructor argument. The Runner callable is invoked under the
  configured profile's ``cwd``.
- :class:`SubprocessRunner` (the validation runner used by slice-9)
  — accepts an optional ``sandbox`` constructor argument. The
  ``run(command)`` method delegates to the sandbox executor with
  a default ``SandboxProfile(cwd=Path.cwd())``.
"""

from __future__ import annotations

from pathlib import Path


class TestTaskExecutionServiceWiring:
    """TaskExecutionService accepts an optional sandbox executor."""

    def test_default_sandbox_is_noop(self, tmp_path: Path) -> None:
        from seharness.execution.service import TaskExecutionService  # noqa: PLC0415

        svc = TaskExecutionService(repo_root=tmp_path, execution_root=tmp_path / "exec")
        # Default behaviour: NoopSandbox.
        assert svc.sandbox is not None
        from seharness.sandbox import NoopSandbox  # noqa: PLC0415

        assert isinstance(svc.sandbox, NoopSandbox)

    def test_custom_sandbox_is_stored(self, tmp_path: Path) -> None:
        from seharness.execution.service import TaskExecutionService  # noqa: PLC0415
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        sandbox = SubprocessSandbox()
        profile = SandboxProfile(cwd=str(tmp_path))
        svc = TaskExecutionService(
            repo_root=tmp_path,
            execution_root=tmp_path / "exec",
            sandbox=sandbox,
            sandbox_profile=profile,
        )
        assert svc.sandbox is sandbox
        assert svc.sandbox_profile is profile

    def test_existing_callers_unchanged(
        self,
        tmp_path: Path,
    ) -> None:
        """Backwards compatibility: the old constructor signature still works."""
        from seharness.execution.service import TaskExecutionService  # noqa: PLC0415

        # No sandbox/profile kwargs — must not raise.
        svc = TaskExecutionService(repo_root=tmp_path, execution_root=tmp_path / "exec")
        assert svc.repo_root == tmp_path
        assert svc.execution_root == tmp_path / "exec"


class TestSubprocessRunnerWiring:
    """SubprocessRunner accepts an optional sandbox executor."""

    def test_default_sandbox_is_noop(self) -> None:
        from seharness.sandbox import NoopSandbox  # noqa: PLC0415
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        runner = SubprocessRunner()
        assert runner._sandbox is not None
        assert isinstance(runner._sandbox, NoopSandbox)

    def test_custom_sandbox_is_stored(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        sandbox = SubprocessSandbox()
        profile = SandboxProfile(cwd=str(tmp_path))
        runner = SubprocessRunner(sandbox=sandbox, sandbox_profile=profile)
        assert runner._sandbox is sandbox
        assert runner._sandbox_profile is profile

    def test_runner_with_sandbox_uses_profile_cwd(self, tmp_path: Path) -> None:
        """Runner.run() invokes the sandbox with profile.cwd."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        sandbox = SubprocessSandbox()
        profile = SandboxProfile(cwd=str(tmp_path))
        runner = SubprocessRunner(sandbox=sandbox, sandbox_profile=profile)
        result = runner.run("pwd")
        assert result.exit_code == 0
        assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()

    def test_runner_with_sandbox_scrubs_env(self, tmp_path: Path) -> None:
        """Runner.run() with a sandbox that has a custom deny list scrubs it."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        sentinel = "OPENCLAW_RUNNER_SENTINEL"
        import os

        os.environ[sentinel] = "leaked"
        try:
            sandbox = SubprocessSandbox()
            profile = SandboxProfile(cwd=str(tmp_path), denied_env_vars=(sentinel,))
            runner = SubprocessRunner(sandbox=sandbox, sandbox_profile=profile)
            cmd = (
                "import os,sys; "
                f"sys.stdout.write('present' if os.environ.get({sentinel!r}) else 'absent')"
            )
            result = runner.run(f"python3 -c {cmd!r}")
            assert result.exit_code == 0
            assert result.stdout == "absent"
        finally:
            del os.environ[sentinel]

    def test_runner_backwards_compatible(self) -> None:
        """Existing callers that don't pass sandbox/profile still work."""
        from seharness.validation.runner import SubprocessRunner  # noqa: PLC0415

        runner = SubprocessRunner()
        result = runner.run("echo hi")
        assert result.exit_code == 0
        assert "hi" in result.stdout


class TestSandboxProtocolConformance:
    """NoopSandbox and SubprocessSandbox implement SandboxExecutor."""

    def test_noop_is_sandbox_executor(self) -> None:
        from seharness.sandbox import NoopSandbox, SandboxExecutor  # noqa: PLC0415

        assert isinstance(NoopSandbox(), SandboxExecutor)

    def test_subprocess_is_sandbox_executor(self) -> None:
        from seharness.sandbox import SandboxExecutor, SubprocessSandbox  # noqa: PLC0415

        assert isinstance(SubprocessSandbox(), SandboxExecutor)

    def test_docker_is_sandbox_executor(self) -> None:
        from seharness.sandbox import DockerSandbox, SandboxExecutor  # noqa: PLC0415

        assert isinstance(DockerSandbox(), SandboxExecutor)
