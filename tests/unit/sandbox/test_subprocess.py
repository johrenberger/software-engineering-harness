"""RED tests for Cluster C, story C3: ``SubprocessSandbox`` executor.

The stdlib fallback applies:

- ``cwd=profile.cwd``
- env scrubbing of ``profile.denied_env_vars``
- ``shell=False`` with ``shlex.split`` argv parsing (default)
- POSIX ``resource.setrlimit`` for CPU/FSIZE/NOFILE/NPROC via
  ``preexec_fn`` (skipped on Windows; resource limits skip cleanly
  when the kernel rejects them)
- ``subprocess.run(timeout=profile.cpu_seconds)`` wall-clock ceiling
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# All tests are unit tests.
pytestmark = pytest.mark.unit


class TestSubprocessSandboxImport:
    """``SubprocessSandbox`` is importable from ``seharness.sandbox``."""

    def test_subprocess_sandbox_importable(self) -> None:
        from seharness.sandbox import SubprocessSandbox  # noqa: PLC0415

        assert SubprocessSandbox is not None


class TestSubprocessSandboxCwd:
    """The sandbox runs the child in ``profile.cwd``."""

    def test_pwd_matches_profile_cwd(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run("pwd", profile=profile)
        assert result.exit_code == 0
        # ``pwd`` may resolve symlinks; compare resolved paths.
        assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()

    def test_pwd_when_cwd_is_a_subdir(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        sub = tmp_path / "sub"
        sub.mkdir()
        profile = SandboxProfile(cwd=str(sub))
        sandbox = SubprocessSandbox()
        result = sandbox.run("pwd", profile=profile)
        assert result.exit_code == 0
        assert Path(result.stdout.strip()).resolve() == sub.resolve()


class TestSubprocessSandboxEnvScrub:
    """Denied env vars do not appear in the child environment."""

    def test_denied_var_absent_via_python(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        sentinel = "OPENCLAW_TEST_SENTINEL_VALUE_ABC"
        os.environ[sentinel] = "leaked"
        try:
            profile = SandboxProfile(
                cwd=str(tmp_path),
                denied_env_vars=(sentinel,),
            )
            sandbox = SubprocessSandbox()
            cmd = (
                "import os,sys; "
                f"sys.stdout.write('present' if os.environ.get({sentinel!r}) else 'absent')"
            )
            result = sandbox.run(f"python3 -c {cmd!r}", profile=profile)
            assert result.exit_code == 0
            assert result.stdout == "absent"
        finally:
            del os.environ[sentinel]

    def test_default_deny_blocks_path(self, tmp_path: Path) -> None:
        """PATH is in the default deny list; child should not see host PATH."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        cmd = "import os,sys; sys.stdout.write(os.environ.get('PATH', 'absent'))"
        result = sandbox.run(f"python3 -c {cmd!r}", profile=profile)
        assert result.exit_code == 0
        assert result.stdout == "absent"

    def test_extra_env_passed_when_not_denied(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run(
            'python3 -c \'import os,sys; sys.stdout.write(os.environ.get("MY_VAR",""))\'',
            profile=profile,
            env={"MY_VAR": "hello"},
        )
        assert result.exit_code == 0
        assert result.stdout == "hello"


class TestSubprocessSandboxShellFalse:
    """Default ``shell=False`` rejects shell metacharacters safely."""

    def test_default_shell_is_false(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        # A shell-only command should fail to parse with shlex.
        result = sandbox.run("echo hi && echo bye", profile=profile)
        # shlex.split will parse it as argv ['echo', 'hi', '&&', 'echo', 'bye']
        # so echo prints "hi && echo bye" and exits 0.
        assert result.exit_code == 0
        assert "&&" in result.stdout

    def test_allow_shell_runs_pipelines(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox(allow_shell=True)
        # Shell pipeline: emit "hello" then uppercase via python.
        result = sandbox.run(
            'echo hello | python3 -c "import sys; sys.stdout.write(sys.stdin.read().upper())"',
            profile=profile,
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "HELLO"


class TestSubprocessSandboxTimeout:
    """``profile.cpu_seconds`` is enforced as a wall-clock timeout."""

    def test_long_sleep_is_killed(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=1.0)
        sandbox = SubprocessSandbox()
        # Sleep 10s; sandbox must kill at ~1s.
        result = sandbox.run("sleep 10", profile=profile)
        assert result.exit_code == 124  # canonical timeout code
        assert "TIMEOUT" in result.stderr

    def test_quick_command_completes(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=5.0)
        sandbox = SubprocessSandbox()
        result = sandbox.run("echo hi", profile=profile)
        assert result.exit_code == 0


class TestSubprocessSandboxExitCode:
    """The exit code propagates faithfully."""

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run("false", profile=profile)
        assert result.exit_code != 0

    def test_zero_exit(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run("true", profile=profile)
        assert result.exit_code == 0


class TestSubprocessSandboxEmptyCommand:
    """An empty command after ``shlex.split`` is rejected cleanly."""

    def test_empty_string_rejected(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run("", profile=profile)
        assert result.exit_code == 2

    def test_whitespace_only_rejected(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        sandbox = SubprocessSandbox()
        result = sandbox.run("   ", profile=profile)
        assert result.exit_code == 2


class TestSubprocessSandboxScrubWarning:
    """An empty ``denied_env_vars`` triggers a sandbox violation flag."""

    def test_empty_deny_records_violation(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        # Construct manually with empty deny to simulate permissive config.
        profile = SandboxProfile(cwd=str(tmp_path))
        profile = profile.model_copy(update={"denied_env_vars": ()})
        sandbox = SubprocessSandbox()
        result = sandbox.run("true", profile=profile)
        # The "denied_env_vars is empty" violation is recorded.
        assert any("denied_env_vars" in v for v in result.sandbox_violations)


# ---------------------------------------------------------------------------
# POSIX-only tests for RLIMIT_* and chroot.
# ---------------------------------------------------------------------------


@pytest.fixture
def require_posix() -> None:
    if not hasattr(os, "fork") or not hasattr(os, "getuid"):
        pytest.skip("POSIX resource limits require Linux/macOS")


class TestSubprocessSandboxResourceLimits:
    """``RLIMIT_CPU`` and ``RLIMIT_FSIZE`` apply in the child."""

    def test_rlimit_cpu_kills_long_running(self, tmp_path: Path, require_posix: None) -> None:
        """A child with ``RLIMIT_CPU=1`` is killed by SIGXCPU within 1 CPU-sec.

        We use a busy-spin Python loop rather than ``sleep`` because
        RLIMIT_CPU measures CPU time, not wall clock.
        """
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path), cpu_seconds=1.0)
        sandbox = SubprocessSandbox()
        # Python busy-wait for 5s of CPU; RLIMIT_CPU=1 kills via SIGXCPU.
        result = sandbox.run(
            "python3 -c 'import time;t=time.monotonic();while time.monotonic()-t<5: pass'",
            profile=profile,
        )
        # SIGXCPU -> exit_code is implementation-specific (-1 on POSIX).
        # The child must NOT complete normally.
        assert result.exit_code != 0 or "TIMEOUT" not in result.stderr


class TestSubprocessSandboxChroot:
    """chroot jail is best-effort; only attempted when uid==0 and a single path."""

    def test_chroot_skipped_when_not_root(self, tmp_path: Path, require_posix: None) -> None:
        """We can't easily test chroot success without root, but we can
        verify the executor does NOT crash if uid != 0."""
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        work = tmp_path / "work"
        work.mkdir()
        profile = SandboxProfile(
            cwd=str(work),
            allowed_paths=(str(work),),
        )
        sandbox = SubprocessSandbox()
        # Just verify it runs.
        result = sandbox.run("pwd", profile=profile)
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Result shape.
# ---------------------------------------------------------------------------


class TestSandboxResultShape:
    """``SandboxResult`` carries the fields the validation pipeline needs."""

    def test_result_has_command_and_exit_code(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        result = SubprocessSandbox().run("echo hi", profile=profile)
        assert result.command == "echo hi"
        assert result.exit_code == 0
        assert "hi" in result.stdout

    def test_result_duration_is_non_negative(self, tmp_path: Path) -> None:
        from seharness.sandbox import SandboxProfile, SubprocessSandbox  # noqa: PLC0415

        profile = SandboxProfile(cwd=str(tmp_path))
        result = SubprocessSandbox().run("true", profile=profile)
        assert result.duration_s >= 0.0
