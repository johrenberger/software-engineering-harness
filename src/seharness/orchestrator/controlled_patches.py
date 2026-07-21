"""Cluster N PR5 \u2014 controlled patch generation.

Cluster N of the MiniMax SE-harness improvement handoff.
**Step 5** of the targeted refinement workplan: split
implementation into test-patch and production-patch requests.

The workplan requires that the model produces a unified diff,
not a direct file write. The harness MUST:

- Accept the diff as a :class:`UnifiedDiffSchema` pydantic
  model (extra-forbid, frozen).
- Parse the diff into a :class:`ParsedPatch` and verify the
  declared ``target_paths`` are the only paths touched
  (``PatchValidator.validate_purity``).
- Verify every touched path falls within the operator-declared
  policy (``PatchPolicyChecker.check_paths_within_policy``).
- Apply the diff in a sandboxed directory, never directly on
  the orchestrator's working tree (``SandboxPatchApplier``).
- Persist a :class:`PatchEvidence` record with the diff SHA-256
  hash + provenance (run id, task id, model, template version).

The exit criterion: a fixture test patch and production patch
can be generated and applied without arbitrary direct file
writes.
"""

from __future__ import annotations

import hashlib
import re
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base model that forbids any keys not declared on the schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class UnifiedDiffSchema(_StrictModel):
    """Structured patch the model MUST produce.

    Fields:

    - ``diff_text``: the unified diff body. Must contain at
      least one ``diff --git`` / ``--- `` / ``+++ `` header so
      :class:`PatchValidator` can parse it.
    - ``task_id``: the plan task the patch implements.
    - ``kind``: ``\"test_patch\"`` or ``\"production_patch\"``;
      ``MixedPatchError`` is raised on any other value (closed
      Literal).
    - ``target_paths``: the paths the patch intends to touch.
      :class:`PatchValidator` checks the parsed diff does not
      touch any path outside this set.
    """

    diff_text: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    kind: Literal["test_patch", "production_patch"]
    target_paths: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_diff_has_unified_headers(self) -> UnifiedDiffSchema:
        """Reject a diff body that doesn't carry a ``--- `` /
        ``+++ `` pair. The validator only accepts text that
        looks like a real unified diff so the downstream
        :class:`PatchValidator` can parse it deterministically."""
        if "--- " not in self.diff_text or "+++ " not in self.diff_text:
            msg = "diff_text missing unified-diff headers (--- / +++)"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# Parsed patch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedPatch:
    """A parsed unified diff.

    Captures the set of paths the diff actually touches
    (``touched_paths``) and the per-path operations
    (``additions``, ``modifications``, ``deletions``).

    The parser is intentionally simple \u2014 it splits the diff
    body on the ``diff --git`` / ``--- `` / ``+++ `` markers
    and extracts the destination paths. It does NOT attempt to
    resolve renames or binary diffs; the workplan's controlled
    patches are text-only by design.
    """

    diff_text: str
    additions: tuple[str, ...]
    modifications: tuple[str, ...]
    deletions: tuple[str, ...]

    @property
    def touched_paths(self) -> tuple[str, ...]:
        """Paths touched by the diff, in source order."""
        # Use a tuple to preserve order while de-duplicating.
        seen: dict[str, None] = {}
        for path in (*self.additions, *self.modifications, *self.deletions):
            if path not in seen:
                seen[path] = None
        return tuple(seen)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


_DIFF_HEADER_RE = re.compile(r"^\+\+\+\s+(\S+)", re.MULTILINE)


class PatchValidator:
    """Parse unified diffs and check their purity against the
    model's declared ``target_paths``."""

    @staticmethod
    def parse(diff_text: str) -> ParsedPatch:
        """Parse ``diff_text`` and return a :class:`ParsedPatch`.

        The parser uses the ``+++ `` lines as the destination
        path marker. ``--- `` lines are the source path (often
        ``/dev/null`` for new files). Additions are inferred
        from ``/dev/null`` sources; deletions from ``/dev/null``
        destinations; everything else is a modification.
        """
        if not diff_text:
            msg = "diff_text is empty"
            raise ValueError(msg)
        additions: list[str] = []
        modifications: list[str] = []
        deletions: list[str] = []
        # Walk the diff line-by-line. ``+++ <path>`` is the new
        # file path; ``--- <path>`` is the old file path. When
        # ``--- /dev/null``, the diff adds the file. When
        # ``+++ /dev/null``, the diff deletes it. Otherwise it's
        # a modification.
        lines = diff_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("--- "):
                old_path = line[4:].split("\t", 1)[0].strip()
                # The next line MUST be ``+++ <new>`` for a
                # well-formed diff; we tolerate its absence by
                # skipping.
                if i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
                    new_path = lines[i + 1][4:].split("\t", 1)[0].strip()
                    if old_path == "/dev/null" and new_path != "/dev/null":
                        additions.append(_strip_diff_prefix(new_path))
                    elif new_path == "/dev/null" and old_path != "/dev/null":
                        deletions.append(_strip_diff_prefix(old_path))
                    elif new_path != "/dev/null":
                        modifications.append(_strip_diff_prefix(new_path))
                    i += 2
                    continue
            i += 1
        if not additions and not modifications and not deletions:
            msg = "diff_text contains no parsable --- / +++ pairs"
            raise ValueError(msg)
        return ParsedPatch(
            diff_text=diff_text,
            additions=tuple(additions),
            modifications=tuple(modifications),
            deletions=tuple(deletions),
        )

    @staticmethod
    def validate_purity(
        parsed: ParsedPatch,
        declared_target_paths: Sequence[str],
    ) -> None:
        """Reject a diff that touches a path outside the model's
        declared ``target_paths``.

        Per the workplan: "the model MUST declare the paths it
        intends to touch, and the harness MUST reject any diff
        that touches additional paths". This catches a model
        that smuggles in an extra file change while claiming
        ``target_paths=[\"foo.py\"]``.
        """
        declared = set(declared_target_paths)
        offending = [path for path in parsed.touched_paths if path not in declared]
        if offending:
            msg = f"diff touches paths outside declared target_paths {list(declared)}: {offending}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Policy checker
# ---------------------------------------------------------------------------


class PatchPolicyChecker:
    """Reject a diff that touches paths outside the
    operator-declared policy.

    Mirrors :func:`validate_plan_against_policy` from
    ``spec_plan_schemas.py``. The check is ``startswith``-based;
    absolute paths require an explicit absolute-policy entry.
    """

    @staticmethod
    def check_paths_within_policy(
        parsed: ParsedPatch,
        *,
        policy_allowed_paths: Sequence[str],
    ) -> None:
        """Reject any touched path that is not within
        ``policy_allowed_paths``."""
        if not policy_allowed_paths:
            msg = "policy_allowed_paths is empty; refusing to validate patch"
            raise ValueError(msg)
        offending = [
            path
            for path in parsed.touched_paths
            if not any(path.startswith(prefix) for prefix in policy_allowed_paths)
        ]
        if offending:
            msg = f"patch touches paths outside policy {list(policy_allowed_paths)}: {offending}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Sandboxed application
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatchApplicationResult:
    """Outcome of applying a patch in a sandbox.

    ``hash`` is the SHA-256 of the diff body so the orchestrator
    can prove the patch it applied is byte-for-byte identical to
    the one the model produced. ``applied_paths`` are the paths
    that ended up in the sandbox directory after ``git apply``.
    """

    hash: str
    applied_paths: tuple[str, ...]
    sandbox_dir: str
    template_version: str


class SandboxPatchApplier:
    """Apply a :class:`ParsedPatch` inside a sandbox directory.

    The applier writes the diff body to ``<sandbox>/change.patch``
    and runs ``git apply --check`` followed by ``git apply``. If
    ``--check`` fails the diff is rejected before any files are
    written. The applier records the SHA-256 of the diff body
    so the orchestrator can prove the diff it applied matches
    the one the model produced (no silent mutation).
    """

    def __init__(
        self,
        *,
        sandbox_dir: Path,
        template_version: str = "controlled-patches@v1",
        runner: SupportsGitApply | None = None,
    ) -> None:
        self._sandbox_dir = Path(sandbox_dir)
        self._template_version = template_version
        self._runner = runner or _SubprocessGitApply()

    def apply(self, parsed: ParsedPatch) -> PatchApplicationResult:
        """Apply ``parsed`` to the sandbox. Returns the SHA-256
        hash and the set of paths that ended up in the sandbox.

        The applier MUST NOT mutate the orchestrator's working
        tree. ``sandbox_dir`` is owned by the caller; the applier
        writes under it and never above it.
        """
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)
        patch_path = self._sandbox_dir / "change.patch"
        diff_bytes = parsed.diff_text.encode("utf-8")
        patch_path.write_bytes(diff_bytes)
        # Verify the diff applies before mutating anything.
        self._runner.check(self._sandbox_dir, patch_path)
        self._runner.apply(self._sandbox_dir, patch_path)
        return PatchApplicationResult(
            hash=hashlib.sha256(diff_bytes).hexdigest(),
            applied_paths=parsed.touched_paths,
            sandbox_dir=str(self._sandbox_dir),
            template_version=self._template_version,
        )


@runtime_checkable
class SupportsGitApply(Protocol):
    """Minimal surface for a git-apply runner."""

    def check(self, repo_dir: Path, patch_path: Path) -> None:
        """Run ``git apply --check``; raise on failure."""
        ...

    def apply(self, repo_dir: Path, patch_path: Path) -> None:
        """Run ``git apply``; raise on failure."""
        ...


class _SubprocessGitApply:
    """Default runner that shells out to ``git apply``."""

    def check(self, repo_dir: Path, patch_path: Path) -> None:
        result = subprocess.run(  # nosec B603 B607 -- fixed argv, partial path is the documented git binary
            [
                "git",
                "apply",
                "--check",
                str(patch_path),
            ],
            cwd=str(repo_dir),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"git apply --check failed: {result.stderr or result.stdout}"
            raise ValueError(msg)

    def apply(self, repo_dir: Path, patch_path: Path) -> None:
        result = subprocess.run(  # nosec B603 B607 -- fixed argv, partial path is the documented git binary
            ["git", "apply", str(patch_path)],
            cwd=str(repo_dir),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"git apply failed: {result.stderr or result.stdout}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class PatchEvidence(_StrictModel):
    """Persisted record of a controlled patch application.

    The orchestrator writes this to ``<run_dir>/patch-evidence.json``
    so the dashboard / PR comment / audit trail can verify the
    exact diff that was applied, the model that produced it,
    and the run it belongs to.
    """

    hash: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    kind: Literal["test_patch", "production_patch"]
    run_id: str = Field(min_length=1)
    applied_paths: tuple[str, ...]
    sandbox_dir: str = Field(min_length=1)
    template_version: str = Field(min_length=1)
    model: str | None = None
    provider: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_diff_prefix(path: str) -> str:
    """Strip the ``a/`` or ``b/`` prefix from a diff path.

    ``git diff`` outputs ``a/src/foo.py`` and ``b/src/foo.py``;
    the path the harness cares about is ``src/foo.py``.
    """
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def parse_unified_diff(payload: dict[str, object]) -> UnifiedDiffSchema:
    """Parse a model payload (dict) into a
    :class:`UnifiedDiffSchema`. Raises ``ValueError`` on schema
    mismatch (including the closed-Literal ``kind`` check)."""
    return UnifiedDiffSchema.model_validate(payload)


__all__ = [
    "ParsedPatch",
    "PatchApplicationResult",
    "PatchEvidence",
    "PatchPolicyChecker",
    "PatchValidator",
    "SandboxPatchApplier",
    "SupportsGitApply",
    "UnifiedDiffSchema",
    "parse_unified_diff",
]
