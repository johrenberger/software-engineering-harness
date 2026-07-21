"""Cluster N PR5 \u2014 controlled patch generation tests.

Pins the workplan Step 5 exit criterion: a fixture test patch
and production patch can be generated and applied without
arbitrary direct file writes.

The tests cover:

- :class:`UnifiedDiffSchema` accepts a well-formed diff and
  rejects a malformed one (closed Literal on ``kind``).
- :class:`PatchValidator.parse` extracts the touched paths
  from a unified diff.
- :class:`PatchValidator.validate_purity` rejects a diff that
  touches paths outside the declared ``target_paths``.
- :class:`PatchPolicyChecker.check_paths_within_policy`
  rejects a diff that touches paths outside the policy.
- :class:`SandboxPatchApplier.apply` records the SHA-256 hash
  of the diff body and applies it via ``git apply``.
- :class:`PatchEvidence` carries the hash + provenance.

The tests use a synthetic :class:`FakeGitApply` runner so the
sandbox layer never shells out to ``git apply`` in unit tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import seharness.controller.run_ledger  # noqa: F401  -- import-order trigger
from seharness.orchestrator.controlled_patches import (
    ParsedPatch,
    PatchApplicationResult,
    PatchEvidence,
    PatchPolicyChecker,
    PatchValidator,
    SandboxPatchApplier,
    SupportsGitApply,
    UnifiedDiffSchema,
    parse_unified_diff,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_TEST_PATCH = """\
diff --git a/tests/test_foo.py b/tests/test_foo.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/tests/test_foo.py
@@ -0,0 +1,5 @@
+def test_foo():
+    assert 1 + 1 == 2
+    assert True
+
+def test_bar():
+    assert 2 + 2 == 4
"""

_PRODUCTION_PATCH = """\
diff --git a/src/foo.py b/src/foo.py
new file mode 100644
index 0000000..def5678
--- /dev/null
+++ b/src/foo.py
@@ -0,0 +1,3 @@
+def add(a, b):
+    return a + b
+
"""


_MODIFY_PATCH = """\
diff --git a/src/foo.py b/src/foo.py
index 1234567..89abcde 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,1 +1,2 @@
 def add(a, b):
+    return a + b
"""


_DELETE_PATCH = """\
diff --git a/src/old.py b/src/old.py
deleted file mode 100644
index 1234567..0000000
--- a/src/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-# deprecated
"""


class _FakeGitApply:
    """In-process ``git apply`` runner for unit tests.

    Records every (repo_dir, patch_path) tuple so tests can
    assert what the applier did. ``--check`` always passes by
    default; ``apply`` is a no-op (the sandbox is just a
    directory, not a real git repo).
    """

    def __init__(self, *, check_passes: bool = True) -> None:
        self.check_calls: list[tuple[Path, Path]] = []
        self.apply_calls: list[tuple[Path, Path]] = []
        self._check_passes = check_passes

    def check(self, repo_dir: Path, patch_path: Path) -> None:
        self.check_calls.append((repo_dir, patch_path))
        if not self._check_passes:
            msg = "git apply --check failed (fake runner)"
            raise ValueError(msg)

    def apply(self, repo_dir: Path, patch_path: Path) -> None:
        self.apply_calls.append((repo_dir, patch_path))


# ---------------------------------------------------------------------------
# UnifiedDiffSchema contract
# ---------------------------------------------------------------------------


class TestUnifiedDiffSchemaContract:
    """The schema accepts a well-formed diff and rejects
    malformed input. ``kind`` is a closed Literal so the model
    cannot pick ``\"feature_patch\"`` or similar."""

    def test_accepts_test_patch(self) -> None:
        schema = UnifiedDiffSchema(
            diff_text=_TEST_PATCH,
            task_id="task-1",
            kind="test_patch",
            target_paths=("tests/test_foo.py",),
        )
        assert schema.task_id == "task-1"
        assert schema.kind == "test_patch"
        assert schema.target_paths == ("tests/test_foo.py",)

    def test_accepts_production_patch(self) -> None:
        schema = UnifiedDiffSchema(
            diff_text=_PRODUCTION_PATCH,
            task_id="task-2",
            kind="production_patch",
            target_paths=("src/foo.py",),
        )
        assert schema.kind == "production_patch"

    def test_rejects_unknown_kind(self) -> None:
        """Per cluster N, ``kind`` is a closed Literal."""
        with pytest.raises(ValidationError):
            UnifiedDiffSchema(
                diff_text=_TEST_PATCH,
                task_id="task-1",
                kind="feature_patch",  # not in the closed set
                target_paths=("tests/test_foo.py",),
            )

    def test_rejects_missing_diff_headers(self) -> None:
        """A diff body without ``--- `` / ``+++ `` is rejected
        so the downstream parser cannot fail silently."""

        with pytest.raises(ValidationError) as excinfo:
            UnifiedDiffSchema(
                diff_text="this is not a diff",
                task_id="task-1",
                kind="test_patch",
                target_paths=(),
            )
        assert "unified-diff headers" in str(excinfo.value)

    def test_rejects_empty_diff_text(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedDiffSchema(
                diff_text="",
                task_id="task-1",
                kind="test_patch",
            )

    def test_rejects_extra_keys(self) -> None:
        """The schema is ``extra=forbid`` so the model cannot
        smuggle in undeclared fields (e.g. ``bypass_policy=True``)."""

        with pytest.raises(ValidationError):
            UnifiedDiffSchema(
                diff_text=_TEST_PATCH,
                task_id="task-1",
                kind="test_patch",
                target_paths=("tests/test_foo.py",),
                bypass_policy=True,
            )


# ---------------------------------------------------------------------------
# PatchValidator
# ---------------------------------------------------------------------------


class TestPatchValidatorParse:
    """Parse a unified diff into a :class:`ParsedPatch` with the
    touched paths grouped by operation."""

    def test_parses_test_patch_addition(self) -> None:
        parsed = PatchValidator.parse(_TEST_PATCH)
        assert isinstance(parsed, ParsedPatch)
        assert parsed.additions == ("tests/test_foo.py",)
        assert parsed.modifications == ()
        assert parsed.deletions == ()
        assert parsed.touched_paths == ("tests/test_foo.py",)

    def test_parses_production_patch_addition(self) -> None:
        parsed = PatchValidator.parse(_PRODUCTION_PATCH)
        assert parsed.additions == ("src/foo.py",)

    def test_parses_modification(self) -> None:
        parsed = PatchValidator.parse(_MODIFY_PATCH)
        assert parsed.modifications == ("src/foo.py",)
        assert parsed.additions == ()

    def test_parses_deletion(self) -> None:
        parsed = PatchValidator.parse(_DELETE_PATCH)
        assert parsed.deletions == ("src/old.py",)
        assert parsed.additions == ()

    def test_strips_a_b_diff_prefix(self) -> None:
        """``git diff`` outputs ``a/...`` and ``b/...`` paths;
        the validator strips these so the policy check matches
        against ``src/foo.py`` rather than ``a/src/foo.py``."""

        parsed = PatchValidator.parse(_PRODUCTION_PATCH)
        assert parsed.additions == ("src/foo.py",)
        assert "a/" not in parsed.touched_paths[0]
        assert "b/" not in parsed.touched_paths[0]

    def test_rejects_empty_diff(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            PatchValidator.parse("")

    def test_rejects_diff_without_headers(self) -> None:
        with pytest.raises(ValueError, match="no parsable"):
            PatchValidator.parse("this is not a diff\n")


class TestPatchValidatorPurity:
    """Per the workplan: the model MUST declare the paths it
    intends to touch, and the harness MUST reject any diff that
    touches additional paths."""

    def test_pure_diff_is_accepted(self) -> None:
        parsed = PatchValidator.parse(_TEST_PATCH)
        # ``target_paths`` matches the diff exactly.
        PatchValidator.validate_purity(
            parsed,
            declared_target_paths=("tests/test_foo.py",),
        )

    def test_extra_path_in_diff_is_rejected(self) -> None:
        parsed = PatchValidator.parse(_TEST_PATCH)
        # Model declared target_paths=[] but the diff touches
        # tests/test_foo.py.
        with pytest.raises(ValueError) as excinfo:
            PatchValidator.validate_purity(
                parsed,
                declared_target_paths=(),
            )
        msg = str(excinfo.value)
        assert "tests/test_foo.py" in msg
        assert "declared target_paths" in msg

    def test_smuggled_path_is_rejected(self) -> None:
        """Two-file diff where the model declared only one path."""

        two_file_patch = _TEST_PATCH + _PRODUCTION_PATCH.replace(
            "diff --git a/src/foo.py b/src/foo.py",
            "diff --git a/src/foo.py b/src/foo.py",
        )
        parsed = PatchValidator.parse(two_file_patch)
        # The diff touches both ``src/foo.py`` and
        # ``tests/test_foo.py``. The model declared only the
        # test path \u2014 the production patch is a smuggled
        # change.
        with pytest.raises(ValueError) as excinfo:
            PatchValidator.validate_purity(
                parsed,
                declared_target_paths=("tests/test_foo.py",),
            )
        assert "src/foo.py" in str(excinfo.value)


# ---------------------------------------------------------------------------
# PatchPolicyChecker
# ---------------------------------------------------------------------------


class TestPatchPolicyChecker:
    """The policy check mirrors
    :func:`validate_plan_against_policy` \u2014 every touched path
    must fall within the operator-declared policy."""

    def test_in_policy_paths_are_accepted(self) -> None:
        parsed = PatchValidator.parse(_TEST_PATCH)
        PatchPolicyChecker.check_paths_within_policy(parsed, policy_allowed_paths=("tests/",))
        parsed_prod = PatchValidator.parse(_PRODUCTION_PATCH)
        PatchPolicyChecker.check_paths_within_policy(parsed_prod, policy_allowed_paths=("src/",))

    def test_out_of_policy_paths_are_rejected(self) -> None:
        """A patch touching ``deploy/`` is rejected when the
        policy is ``src/`` + ``tests/``."""

        deploy_patch = """\
diff --git a/deploy/prod.sh b/deploy/prod.sh
new file mode 100644
--- /dev/null
+++ b/deploy/prod.sh
@@ -0,0 +1,1 @@
+# deploy
"""
        parsed = PatchValidator.parse(deploy_patch)
        with pytest.raises(ValueError) as excinfo:
            PatchPolicyChecker.check_paths_within_policy(
                parsed,
                policy_allowed_paths=("src/", "tests/"),
            )
        msg = str(excinfo.value)
        assert "deploy/prod.sh" in msg
        assert "outside policy" in msg

    def test_empty_policy_rejects_everything(self) -> None:
        parsed = PatchValidator.parse(_TEST_PATCH)
        with pytest.raises(ValueError, match="policy_allowed_paths is empty"):
            PatchPolicyChecker.check_paths_within_policy(parsed, policy_allowed_paths=())


# ---------------------------------------------------------------------------
# SandboxPatchApplier
# ---------------------------------------------------------------------------


class TestSandboxPatchApplier:
    """The applier writes the diff to ``<sandbox>/change.patch``,
    runs ``git apply --check`` then ``git apply``, and records
    the SHA-256 of the diff body."""

    def test_apply_writes_diff_and_records_hash(self, tmp_path: Path) -> None:
        runner = _FakeGitApply()
        applier = SandboxPatchApplier(sandbox_dir=tmp_path, runner=runner)
        parsed = PatchValidator.parse(_TEST_PATCH)
        result = applier.apply(parsed)

        assert isinstance(result, PatchApplicationResult)
        # Hash matches the diff body.
        expected_hash = hashlib.sha256(_TEST_PATCH.encode("utf-8")).hexdigest()
        assert result.hash == expected_hash
        # Touched paths are recorded.
        assert result.applied_paths == ("tests/test_foo.py",)
        # The diff was written under the sandbox.
        assert (tmp_path / "change.patch").exists()
        # ``--check`` ran before ``apply``.
        assert len(runner.check_calls) == 1
        assert len(runner.apply_calls) == 1

    def test_apply_fails_when_check_fails(self, tmp_path: Path) -> None:
        runner = _FakeGitApply(check_passes=False)
        applier = SandboxPatchApplier(sandbox_dir=tmp_path, runner=runner)
        parsed = PatchValidator.parse(_TEST_PATCH)
        with pytest.raises(ValueError, match="git apply --check failed"):
            applier.apply(parsed)
        # ``apply`` is NOT called when ``--check`` fails.
        assert len(runner.apply_calls) == 0

    def test_apply_uses_real_git_runner_when_none_provided(self, tmp_path: Path) -> None:
        """When ``runner=None``, the applier falls back to the
        subprocess runner. We don't actually shell out in unit
        tests; we just verify the applier constructs a runner.
        """

        applier = SandboxPatchApplier(sandbox_dir=tmp_path)
        assert isinstance(applier._runner, SupportsGitApply)

    def test_apply_never_writes_outside_sandbox(self, tmp_path: Path) -> None:
        """The applier writes ``<sandbox>/change.patch``; it
        MUST NOT touch the orchestrator's working tree."""

        # Use a sentinel directory to assert no writes outside.
        sentinel = tmp_path / "sentinel"
        sentinel.mkdir()
        sandbox = tmp_path / "sandbox"
        runner = _FakeGitApply()
        applier = SandboxPatchApplier(sandbox_dir=sandbox, runner=runner)
        parsed = PatchValidator.parse(_TEST_PATCH)
        applier.apply(parsed)
        # Sentinel is untouched.
        assert list(sentinel.iterdir()) == []


# ---------------------------------------------------------------------------
# PatchEvidence
# ---------------------------------------------------------------------------


class TestPatchEvidence:
    """The evidence record carries the hash + provenance so the
    dashboard / audit trail can verify what was applied."""

    def test_records_all_required_fields(self) -> None:
        evidence = PatchEvidence(
            hash="abc123",
            task_id="task-1",
            kind="test_patch",
            run_id="orch-abc",
            applied_paths=("tests/test_foo.py",),
            sandbox_dir="/tmp/sandbox",
            template_version="controlled-patches@v1",
            model="MiniMax-M2.7",
            provider="minimax",
        )
        assert evidence.hash == "abc123"
        assert evidence.task_id == "task-1"
        assert evidence.kind == "test_patch"
        assert evidence.run_id == "orch-abc"

    def test_kind_is_closed_literal(self) -> None:
        with pytest.raises(ValidationError):
            PatchEvidence(
                hash="abc123",
                task_id="task-1",
                kind="other",  # not in the closed set
                run_id="orch-abc",
                applied_paths=(),
                sandbox_dir="/tmp",
                template_version="v1",
            )


# ---------------------------------------------------------------------------
# parse_unified_diff helper
# ---------------------------------------------------------------------------


class TestParseUnifiedDiffHelper:
    """``parse_unified_diff`` accepts a ``dict`` payload and
    returns a validated schema."""

    def test_accepts_valid_payload(self) -> None:
        schema = parse_unified_diff(
            {
                "diff_text": _TEST_PATCH,
                "task_id": "task-1",
                "kind": "test_patch",
                "target_paths": ["tests/test_foo.py"],
            }
        )
        assert isinstance(schema, UnifiedDiffSchema)

    def test_rejects_missing_task_id(self) -> None:
        with pytest.raises(ValidationError):
            parse_unified_diff(
                {
                    "diff_text": _TEST_PATCH,
                    "kind": "test_patch",
                    "target_paths": [],
                }
            )


# ---------------------------------------------------------------------------
# End-to-end: full controlled-patch pipeline (offline)
# ---------------------------------------------------------------------------


class TestControlledPatchPipelineOffline:
    """End-to-end exercise of the pipeline against an in-memory
    runner. Verifies the workplan exit criterion: a fixture test
    patch and production patch can be generated and applied
    without arbitrary direct file writes."""

    def test_test_patch_pipeline(self, tmp_path: Path) -> None:
        runner = _FakeGitApply()
        # 1. Model produces the diff (we supply the fixture).
        schema = parse_unified_diff(
            {
                "diff_text": _TEST_PATCH,
                "task_id": "task-1",
                "kind": "test_patch",
                "target_paths": ["tests/test_foo.py"],
            }
        )
        # 2. Parse the diff.
        parsed = PatchValidator.parse(schema.diff_text)
        # 3. Purity check (diff matches declared target_paths).
        PatchValidator.validate_purity(
            parsed,
            declared_target_paths=schema.target_paths,
        )
        # 4. Policy check.
        PatchPolicyChecker.check_paths_within_policy(
            parsed,
            policy_allowed_paths=("tests/",),
        )
        # 5. Apply in a sandbox.
        applier = SandboxPatchApplier(
            sandbox_dir=tmp_path / "sandbox",
            runner=runner,
        )
        result = applier.apply(parsed)
        # 6. Record evidence.
        evidence = PatchEvidence(
            hash=result.hash,
            task_id=schema.task_id,
            kind=schema.kind,
            run_id="orch-test",
            applied_paths=result.applied_paths,
            sandbox_dir=result.sandbox_dir,
            template_version=result.template_version,
        )
        # All steps succeeded and the evidence is complete.
        assert evidence.hash == result.hash
        assert evidence.kind == "test_patch"
        assert evidence.applied_paths == ("tests/test_foo.py",)

    def test_production_patch_pipeline(self, tmp_path: Path) -> None:
        runner = _FakeGitApply()
        schema = parse_unified_diff(
            {
                "diff_text": _PRODUCTION_PATCH,
                "task_id": "task-2",
                "kind": "production_patch",
                "target_paths": ["src/foo.py"],
            }
        )
        parsed = PatchValidator.parse(schema.diff_text)
        PatchValidator.validate_purity(
            parsed,
            declared_target_paths=schema.target_paths,
        )
        PatchPolicyChecker.check_paths_within_policy(
            parsed,
            policy_allowed_paths=("src/",),
        )
        applier = SandboxPatchApplier(
            sandbox_dir=tmp_path / "sandbox",
            runner=runner,
        )
        result = applier.apply(parsed)
        assert result.applied_paths == ("src/foo.py",)
