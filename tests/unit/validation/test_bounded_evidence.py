"""RED \u2014 Slice 7 bullet 2: remediation receives only bounded evidence.

Per SPEC \u00a7"Remediation controller" and slice 7 RED bullet 2, the
remediation controller must NEVER see the full repository state. It
receives a ``BoundedEvidence`` envelope containing only:

- the ``NormalizedFailure`` (kind, exit_code, command, message)
- the relevant source file paths and CONTENT (not the whole repo)
- the previous GREEN result (if any)
- the allowed paths for remediation

Anything outside this envelope is a leak.

The ``BoundedEvidenceBuilder`` constructs the envelope from a
``NormalizedFailure`` and a snapshot. It enforces:
- paths outside ``allowed_paths`` are filtered out
- file content is truncated to ``max_bytes_per_file``
- total payload is capped at ``max_total_bytes``
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestBoundedEvidenceShape:
    """The envelope exposes only the documented fields."""

    def test_envelope_has_required_fields(self) -> None:
        from seharness.validation.remediation import BoundedEvidence

        env = BoundedEvidence(
            failure=None,  # type: ignore[arg-type]
            relevant_files=(),
            previous_green=None,
            allowed_paths=("src/",),
        )
        assert env.failure is None
        assert tuple(env.relevant_files) == ()
        assert env.previous_green is None
        assert tuple(env.allowed_paths) == ("src/",)


class TestPathFiltering:
    """The builder drops files outside ``allowed_paths``."""

    def test_files_outside_allowed_paths_are_dropped(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import (
            BoundedEvidenceBuilder,
        )
        from seharness.validation.runner import (
            CommandResult,
            NormalizedFailure,
            FailureKind,
        )

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "docs").mkdir()

        src_file = repo / "src" / "foo.py"
        src_file.write_text("def foo() -> int: return 1\n")
        doc_file = repo / "docs" / "spec.md"
        doc_file.write_text("SPEC v1\n")

        failure = NormalizedFailure(
            kind=FailureKind.ASSERTION,
            exit_code=1,
            command="pytest t",
            message="assert foo() == 2",
            source="stderr",
            duration_s=0.42,
        )
        result = CommandResult(
            command="pytest t", exit_code=1, stdout="", stderr="assert foo() == 2\n",
            duration_s=0.42,
        )

        builder = BoundedEvidenceBuilder(
            repo_root=repo,
            allowed_paths=("src/",),
        )
        evidence = builder.build(failure=failure, command_result=result)

        paths = [f.path for f in evidence.relevant_files]
        assert any("src/foo.py" in p for p in paths)
        assert not any("docs/spec.md" in p for p in paths)


class TestContentTruncation:
    """File content is truncated to ``max_bytes_per_file``."""

    def test_large_file_is_truncated(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import BoundedEvidenceBuilder
        from seharness.validation.runner import (
            CommandResult,
            NormalizedFailure,
            FailureKind,
        )

        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        big = repo / "src" / "big.py"
        big.write_text("x = 1\n" * 10_000)  # ~70 KB

        failure = NormalizedFailure(
            kind=FailureKind.ASSERTION, exit_code=1, command="pytest t",
            message="assert x == 2", source="stderr", duration_s=0.42,
        )
        result = CommandResult(
            command="pytest t", exit_code=1, stdout="", stderr="assert x == 2\n",
            duration_s=0.42,
        )

        builder = BoundedEvidenceBuilder(
            repo_root=repo,
            allowed_paths=("src/",),
            max_bytes_per_file=1024,
        )
        evidence = builder.build(failure=failure, command_result=result)
        big_entry = next(f for f in evidence.relevant_files if "big.py" in f.path)
        assert len(big_entry.content_bytes) <= 1024
        assert big_entry.truncated is True


class TestTotalBudgetCap:
    """Total payload is capped at ``max_total_bytes``."""

    def test_total_payload_capped(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import BoundedEvidenceBuilder
        from seharness.validation.runner import (
            CommandResult,
            NormalizedFailure,
            FailureKind,
        )

        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        for i in range(20):
            (repo / "src" / f"file_{i}.py").write_text("y = 1\n" * 1000)

        failure = NormalizedFailure(
            kind=FailureKind.ASSERTION, exit_code=1, command="pytest t",
            message="assert y == 2", source="stderr", duration_s=0.42,
        )
        result = CommandResult(
            command="pytest t", exit_code=1, stdout="", stderr="assert y == 2\n",
            duration_s=0.42,
        )

        builder = BoundedEvidenceBuilder(
            repo_root=repo,
            allowed_paths=("src/",),
            max_bytes_per_file=512,
            max_total_bytes=2048,
        )
        evidence = builder.build(failure=failure, command_result=result)
        total = sum(len(f.content_bytes) for f in evidence.relevant_files)
        assert total <= 2048


class TestNoFullRepoLeak:
    """The envelope never contains a ``full_repo`` or ``all_files`` field."""

    def test_envelope_does_not_leak_full_repo(self, tmp_path: Path) -> None:
        from seharness.validation.remediation import BoundedEvidenceBuilder
        from seharness.validation.runner import (
            CommandResult,
            NormalizedFailure,
            FailureKind,
        )

        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "foo.py").write_text("x = 1\n")
        # Add a big secret outside allowed paths
        (repo / "secrets.env").write_text("API_KEY=secret123\n" * 100)

        failure = NormalizedFailure(
            kind=FailureKind.ASSERTION, exit_code=1, command="pytest t",
            message="assert x == 2", source="stderr", duration_s=0.42,
        )
        result = CommandResult(
            command="pytest t", exit_code=1, stdout="", stderr="assert x == 2\n",
            duration_s=0.42,
        )
        builder = BoundedEvidenceBuilder(
            repo_root=repo, allowed_paths=("src/",),
        )
        evidence = builder.build(failure=failure, command_result=result)

        # Verify the secret file does not appear in any relevant file.
        all_paths = [f.path for f in evidence.relevant_files]
        assert not any("secrets.env" in p for p in all_paths)
        assert "API_KEY" not in str([f.content for f in evidence.relevant_files])