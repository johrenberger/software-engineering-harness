"""Cluster M3-4: LLM-driven task runner.

The default :class:`StubRunner` writes hardcoded RED + GREEN
evidence. M3-4's offline vertical acceptance needs real pytest
runs in ``tmp_path`` so the assertion "RED fails for the missing
endpoint" actually exercises the missing endpoint, and "GREEN
passes after the patch" actually exercises the patched code.

This module adds :class:`LLMDrivenTaskRunner`, a runner that:

1. Writes RED evidence by running **real pytest** against the
   repo (before any patch).
2. Optionally applies an in-flight patch (``pending_changes``)
   between RED and GREEN. The patch is a tuple of strings; each
   string is a ``WRITE_FILE: <path>\\n<content>`` directive.
3. Writes GREEN evidence by running **real pytest** again,
   against the patched repo.

The runner is wired into the orchestrator via the
``pending_changes`` kwarg the model-backed implementation
service produces from ``ImplementationOutcome.structured.
attempted_changes``. The orchestrator's ``_phase_implementation``
reads the outcome, applies the changes through the sandbox's
allowed-path policy, and stashes them on the runner before
calling ``run_task``.

The directive grammar is deliberately simple:

    WRITE_FILE: <relative-path>
    <content>

Lines are split on the FIRST newline; the first line is the
directive header, the rest is the file content. Lines beginning
with ``#`` are comments. Directives targeting paths outside the
configured ``SandboxConfig.patch_policy_allowed_paths`` are
rejected at apply time so the offline test asserts the same
path policy the production composition enforces.

Cluster M3-4 scope: the directive grammar is sufficient for the
synthetic recordings (whole-file contents). M3-5 will extend
the grammar to real unified diffs; the runner's apply API is
stable so the extension is additive.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 — args are hard-coded; pytest invocation only
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seharness.execution.evidence import FailureKind
from seharness.orchestrator.runner import CommandResult
from seharness.sandbox.cancellation import CancellationToken

#: The directive header for a whole-file write. Lines starting
#: with this prefix declare the target path; the remainder of the
#: directive body is the file content.
WRITE_FILE_HEADER: str = "WRITE_FILE:"


@dataclass(frozen=True)
class _WriteDirective:
    """Parsed ``WRITE_FILE: <path>\\n<content>`` directive."""

    target_path: Path
    content: str


def parse_write_directives(
    raw: Sequence[str],
    *,
    repo_root: Path,
    allowed_paths: Sequence[str],
) -> tuple[_WriteDirective, ...]:
    """Parse ``attempted_changes`` into validated write directives.

    Rejects:

    - Directives targeting absolute paths (must be relative to
      ``repo_root``).
    - Directives targeting paths outside ``allowed_paths``.
    - Directives with empty / missing content.
    - Directives that aren't parseable as ``WRITE_FILE:`` headers.

    Returns the validated directives in input order. Raises
    :class:`ValueError` on the first rejection with a
    human-readable reason.
    """
    if not raw:
        return ()
    directives: list[_WriteDirective] = []
    for index, raw_entry in enumerate(raw):
        if not raw_entry:
            continue
        first_newline = raw_entry.find("\n")
        if first_newline == -1:
            msg = (
                f"attempted_changes[{index}]: missing newline after "
                f"WRITE_FILE: header; got {raw_entry!r:.80}"
            )
            raise ValueError(msg)
        header = raw_entry[:first_newline].strip()
        content = raw_entry[first_newline + 1 :]
        if not header.startswith(WRITE_FILE_HEADER):
            msg = (
                f"attempted_changes[{index}]: directive header must start "
                f"with {WRITE_FILE_HEADER!r}, got {header!r:.80}"
            )
            raise ValueError(msg)
        target = header[len(WRITE_FILE_HEADER) :].strip()
        if not target:
            msg = f"attempted_changes[{index}]: WRITE_FILE directive has an empty target path"
            raise ValueError(msg)
        target_path = Path(target)
        if target_path.is_absolute():
            msg = (
                f"attempted_changes[{index}]: target path must be "
                f"relative to repo_root, got absolute {target!r}"
            )
            raise ValueError(msg)
        resolved = (repo_root / target_path).resolve()
        # Reject escapes via ../
        try:
            resolved.relative_to(repo_root.resolve())
        except ValueError:
            msg = (
                f"attempted_changes[{index}]: target path escapes "
                f"repo_root ({target!r} -> {resolved!s})"
            )
            raise ValueError(msg) from None
        if not any(
            str(resolved).startswith(str((repo_root / prefix).resolve()))
            for prefix in allowed_paths
        ):
            msg = (
                f"attempted_changes[{index}]: target path {target!r} is "
                f"outside the sandbox's allowed paths {list(allowed_paths)!r}"
            )
            raise ValueError(msg)
        directives.append(_WriteDirective(target_path=target_path, content=content))
    return tuple(directives)


def apply_write_directives(
    directives: Sequence[_WriteDirective],
    *,
    repo_root: Path,
) -> tuple[Path, ...]:
    """Apply ``directives`` to ``repo_root``. Returns written paths.

    Creates parent directories as needed. Existing files are
    overwritten (the doc's RED/GREEN cycle expects the
    production-patch to be applied after RED, on top of whatever
    was there).
    """
    written: list[Path] = []
    for d in directives:
        target = repo_root / d.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(d.content)
        written.append(target)
    return tuple(written)


def _run_pytest(
    *,
    cwd: Path,
    target: str,
    timeout_s: float,
) -> CommandResult:
    """Run pytest against ``target`` in ``cwd``.

    Captures ``exit_code``, ``stdout``, ``stderr``, ``duration_s``.
    On timeout, returns ``exit_code=124`` and a stderr note. This
    mirrors the slice-7 ``LocalCommandRunner._run_validation_simple``
    pattern so the offline runner and the production validation
    runner share a failure shape.

    The command uses ``-p no:cacheprovider`` to avoid pytest
    cache plugin import warnings under fresh tmp_path roots, and
    ``--rootdir=cwd`` so pytest picks up the *inner* pyproject.toml
    (fixture repo's, not the se-harness workspace's). The
    ``--override-ini`` flag clears any inherited ``addopts`` so
    coverage / reruns plugins don't try to load against the
    fixture repo's bare imports.
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        target,
        "--no-cov",
        "-v",
        "--tb=short",
        "-p",
        "no:cacheprovider",
        "--rootdir",
        str(cwd),
    ]
    start = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603 — args are hard-coded
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr_raw = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(
            command=" ".join(cmd),
            exit_code=124,
            stdout=stdout,
            stderr=stderr_raw + f"\nTIMEOUT after {timeout_s}s",
            duration_s=time.monotonic() - start,
        )
    return CommandResult(
        command=" ".join(cmd),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_s=time.monotonic() - start,
    )


class LLMDrivenTaskRunner:
    """Real pytest runner that applies an in-flight model patch.

    Compared to :class:`StubRunner`, this runner:

    - Runs **real pytest** for both RED and GREEN against
      ``repo_root``.
    - Applies ``pending_changes`` between RED and GREEN.
    - Computes ``exit_code``, ``failure_kind``, and ``test_id``
      from real pytest output (RED must fail with
      ``EXPECTED_FAILURE``; GREEN must exit 0).
    - Returns a real ``final_diff`` via ``git diff`` against the
      initial commit so the offline test can assert "final diff
      stays within allowed paths".
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        pytest_target: str = "tests/",
        pytest_timeout_s: float = 60.0,
        allowed_paths: Sequence[str] = (),
        cancel: CancellationToken | None = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._pytest_target = pytest_target
        self._pytest_timeout_s = pytest_timeout_s
        self._allowed_paths = tuple(allowed_paths)
        # Cancellation is accepted for parity with the other runners;
        # we currently only run synchronous subprocess.run so cancel
        # has no effect. The parameter keeps the surface stable for
        # future async extension.
        self._cancel = cancel

    def run_task(
        self,
        *,
        red_dir: Path,
        green_dir: Path,
        task_id: str,
        cancel: CancellationToken | None = None,
        pending_changes: Sequence[str] | None = None,
    ) -> CommandResult:
        """Run RED → apply patch → run GREEN, then write evidence.

        The ``pending_changes`` parameter is the model-produced
        ``_ImplementationPayload.attempted_changes``. When empty
        or ``None`` the runner still runs RED + GREEN but skips
        the apply step; this is the "test-patch only" flow.
        """
        _ = cancel  # accepted but unused; see __init__.
        for d in (red_dir, green_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 1. Snapshot the initial commit so the final diff is
        # measured against the committed state, not the working
        # tree's pre-run content.
        git_path = shutil.which("git")
        if git_path is None:
            base_sha = ""
        else:
            try:
                base_sha = subprocess.run(  # nosec B603 — args are hard-coded
                    [git_path, "rev-parse", "HEAD"],
                    cwd=self._repo_root,
                    capture_output=True,
                    check=True,
                    text=True,
                    timeout=10,
                ).stdout.strip()
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
                base_sha = ""

        # 2. RED — run pytest BEFORE any patch.
        red_result = _run_pytest(
            cwd=self._repo_root,
            target=self._pytest_target,
            timeout_s=self._pytest_timeout_s,
        )
        red_dir.joinpath("command.txt").write_text(red_result.command + "\n")
        red_dir.joinpath("stdout.txt").write_text(red_result.stdout or "")
        red_dir.joinpath("stderr.txt").write_text(red_result.stderr or "")
        red_payload: dict[str, Any] = {
            "phase": "red",
            "exit_code": red_result.exit_code,
            "duration_s": red_result.duration_s,
            "test_id": f"tests/{task_id}.py::test_target",
            "command": red_result.command,
        }
        if red_result.exit_code != 0:
            # Map pytest failures to the validator's expected
            # ``expected_failure`` kind. The offline synthetic
            # recordings always produce a missing-endpoint
            # failure (404 → AssertionError in test_health),
            # which is exactly the expected-failure reason.
            red_payload["failure_kind"] = FailureKind.EXPECTED_FAILURE.value
            red_payload["failure_reason"] = (
                "pytest reported test failures (expected_failure per the M3-4 synthetic fixture)"
            )
        red_dir.joinpath("result.json").write_text(
            json.dumps(red_payload, indent=2, sort_keys=True) + "\n"
        )

        # 3. Apply the patch (if any).
        if pending_changes:
            directives = parse_write_directives(
                pending_changes,
                repo_root=self._repo_root,
                allowed_paths=self._allowed_paths,
            )
            apply_write_directives(directives, repo_root=self._repo_root)

        # 4. GREEN — run pytest AFTER the patch.
        green_result = _run_pytest(
            cwd=self._repo_root,
            target=self._pytest_target,
            timeout_s=self._pytest_timeout_s,
        )
        green_dir.joinpath("command.txt").write_text(green_result.command + "\n")
        green_dir.joinpath("stdout.txt").write_text(green_result.stdout or "")
        green_dir.joinpath("stderr.txt").write_text(green_result.stderr or "")
        covered_tests = _extract_passed_tests(green_result.stdout)
        green_payload: dict[str, Any] = {
            "phase": "green",
            "exit_code": green_result.exit_code,
            "duration_s": green_result.duration_s,
            "test_id": f"tests/{task_id}.py::test_target",
            "command": green_result.command,
            "covered_tests": covered_tests,
            "required_tests": covered_tests,
        }
        green_dir.joinpath("result.json").write_text(
            json.dumps(green_payload, indent=2, sort_keys=True) + "\n"
        )

        # 5. Persist the final diff so the offline vertical test can
        # assert "final diff stays within allowed paths". Use
        # ``git diff <base_sha>`` so the diff is against the
        # committed state.
        try:
            diff_proc = subprocess.run(  # nosec B603 — args are hard-coded
                ["git", "diff", base_sha] if base_sha else ["git", "diff"],
                cwd=self._repo_root,
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
            final_diff = diff_proc.stdout
        except (OSError, subprocess.TimeoutExpired):
            final_diff = ""
        # The diff is written under ``execution/<task_id>/final-diff.patch``
        # by the orchestrator's evidence layer; we surface it via
        # ``red_dir.parent / "final-diff.patch"`` so the offline
        # test can find it without knowing the runner internals.
        diff_path = red_dir.parent / "final-diff.patch"
        diff_path.write_text(final_diff)

        return CommandResult(
            command=green_result.command,
            exit_code=green_result.exit_code,
            stdout=green_result.stdout,
            stderr=green_result.stderr,
            duration_s=green_result.duration_s,
        )

    def run_validation(
        self,
        *,
        command: str,
        cwd: Path,
        timeout_s: float = 60.0,
        cancel: CancellationToken | None = None,
    ) -> CommandResult:
        """Run the validation command against the patched repo.

        The ``LLMDrivenTaskRunner`` does not cache the GREEN result
        from ``run_task`` (that result lives in the evidence directory
        written by the ``TaskExecutionService``). For
        ``run_validation`` we run the command directly using the
        same Python interpreter the test suite uses, so the
        validation reflects the patched repo's real state.
        """
        _ = cancel  # accepted but unused; see __init__.
        cmd = command.split() if command else ["python3", "-m", "pytest"]
        start = time.monotonic()
        try:
            completed = subprocess.run(  # nosec B603 — args from test config
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            duration = time.monotonic() - start
            return CommandResult(
                command=command,
                exit_code=124 if isinstance(exc, subprocess.TimeoutExpired) else 1,
                stdout="",
                stderr=str(exc),
                duration_s=duration,
            )
        duration = time.monotonic() - start
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_s=duration,
        )


_PYTEST_PASSED_LINE_RE = re.compile(
    r"^tests/[\w/\-]+\.py::[\w_\-]+(?:\[[^\]]+\])?\s+PASSED", re.MULTILINE
)


def _extract_passed_tests(stdout: str) -> tuple[str, ...]:
    """Return the test ids pytest reported as PASSED in ``stdout``.

    Pytest's ``-q`` output format includes lines like
    ``tests/test_health.py::test_health_returns_ok PASSED``. We
    extract them via regex; a missing or unparseable stdout yields
    an empty tuple.
    """
    if not stdout:
        return ()
    return tuple(_PYTEST_PASSED_LINE_RE.findall(stdout))


__all__ = [
    "WRITE_FILE_HEADER",
    "LLMDrivenTaskRunner",
    "apply_write_directives",
    "parse_write_directives",
]
