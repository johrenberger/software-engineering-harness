"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 1.

'Models cannot push':
- The model adapter interface MUST NOT expose push, force-push, or
  PR-creation methods.
- Models only produce text output; the controller is the sole Git
  actor.
"""

from __future__ import annotations

import inspect

import seharness.models.base as base_mod
import seharness.models.fake as fake_mod
from seharness.delivery.pr import PullRequestClient
from seharness.models.base import ModelAdapter
from seharness.models.fake import FakeModelAdapter


def test_model_adapter_interface_has_no_push_method() -> None:
    """ModelAdapter protocol MUST NOT have any push/PR-creating method."""
    forbidden = ("push", "force_push", "create_pr", "open_pr", "merge")
    members = set(dir(ModelAdapter))
    for method in forbidden:
        assert method not in members, (
            f"ModelAdapter exposes forbidden Git method: {method}"
        )


def test_fake_adapter_implementation_has_no_push_method() -> None:
    """FakeModelAdapter (the test impl) MUST NOT implement push methods either."""
    forbidden = ("push", "force_push", "create_pr", "open_pr", "merge")
    for method in forbidden:
        assert not hasattr(FakeModelAdapter, method), (
            f"FakeModelAdapter implements forbidden Git method: {method}"
        )


def test_fake_adapter_invoke_signature_has_no_git_params() -> None:
    """FakeModelAdapter.invoke() signature MUST NOT accept git-related params."""
    sig = inspect.signature(FakeModelAdapter.invoke)
    params = list(sig.parameters)
    forbidden = ("branch", "commit_message", "pr_body", "files")
    for f in forbidden:
        assert f not in params, (
            f"FakeModelAdapter.invoke accepts forbidden git param: {f}"
        )


def test_pull_request_client_has_create_method() -> None:
    """PullRequestClient is a Protocol — production code calls it, models never do."""
    assert callable(getattr(PullRequestClient, "create", None))


def test_pr_client_creation_routes_through_controller_not_model() -> None:
    """PR creation MUST go through PullRequestClient, not via the model adapter."""
    adapter_members = set(dir(FakeModelAdapter))
    assert "create" not in adapter_members
    assert "create_pr" not in adapter_members


def test_models_have_no_git_subprocess_access() -> None:
    """Models MUST NOT import subprocess for Git operations."""
    for mod in (base_mod, fake_mod):
        src = inspect.getsource(mod)
        assert "git push" not in src.lower(), (
            f"{mod.__name__} contains 'git push' — models must not push"
        )
        assert "force-push" not in src.lower()
        assert "create_pr" not in src.lower()
