"""Tests for the BranchService.

Per SPEC §'19. Git Automation':
- Branch format: 'ai/feature/<feature-id>-<slug>' (production)
- Slice 9 tests use 'agent/<NN>-<slug>' convention to match
  current slice-by-slice development workflow.
- BranchService takes BranchFormat as a parameter (B1 decision).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from seharness.delivery.branch import BranchFormat, BranchService
from seharness.delivery.backend import GitBackend


class _FakeBackend(GitBackend):
    def __init__(self) -> None:
        self.created: list[str] = []

    def create_branch(self, repo_root: Path, branch_name: str) -> None:
        self.created.append(branch_name)

    def stage(self, repo_root: Path, files: tuple[str, ...]) -> None:
        pass

    def commit(
        self,
        repo_root: Path,
        message: str,
        *,
        author_name: str,
        author_email: str,
    ) -> str:
        return "deadbeef"

    def current_sha(self, repo_root: Path) -> str:
        return "deadbeef"


def test_branch_format_renders_template() -> None:
    fmt = BranchFormat(template="agent/{slice_number}-{slug}")
    rendered = fmt.render(slice_number="01", slug="config-validation")
    assert rendered == "agent/01-config-validation"


def test_branch_format_default_template() -> None:
    fmt = BranchFormat()
    assert "{slug}" in fmt.template or "{" in fmt.template


def test_branch_format_production_template_matches_spec() -> None:
    """Production format per SPEC §'19. Git Automation':
    'ai/feature/<feature-id>-<slug>'
    """
    fmt = BranchFormat(template="ai/feature/{feature_id}-{slug}")
    rendered = fmt.render(feature_id="F-1", slug="reset-password")
    assert rendered == "ai/feature/F-1-reset-password"


def test_branch_format_missing_placeholder_raises() -> None:
    fmt = BranchFormat(template="agent/{slice_number}-{slug}")
    with pytest.raises(KeyError):
        fmt.render(slug="x")  # missing slice_number


def test_branch_format_rejects_extra_template_field() -> None:
    with pytest.raises(ValidationError):
        BranchFormat(
            template="agent/{slice_number}-{slug}",
            unknown_field="surprise",  # type: ignore[call-arg]
        )


def test_branch_format_is_frozen() -> None:
    fmt = BranchFormat(template="x")
    with pytest.raises(ValidationError):
        fmt.template = "y"  # type: ignore[misc]


def test_branch_service_creates_branch_via_backend(tmp_path: Path) -> None:
    backend = _FakeBackend()
    fmt = BranchFormat(template="agent/{slice_number}-{slug}")
    service = BranchService(backend=backend, branch_format=fmt)
    service.create(
        repo_root=tmp_path, slice_number="01", slug="config-validation"
    )
    assert "agent/01-config-validation" in backend.created


def test_branch_service_returns_branch_name(tmp_path: Path) -> None:
    backend = _FakeBackend()
    fmt = BranchFormat(template="agent/{slice_number}-{slug}")
    service = BranchService(backend=backend, branch_format=fmt)
    name = service.create(
        repo_root=tmp_path, slice_number="01", slug="config"
    )
    assert name == "agent/01-config"