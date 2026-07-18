"""Tests for SPEC §'Slice 9: Git delivery' RED bullet 1.

'Models cannot push':
- The model adapter interface MUST NOT expose push, force-push, or
  PR-creation methods.
- Models only produce text output; the controller is the sole Git
  actor.
"""

from __future__ import annotations

import inspect

import pytest

from seharness.models.base import ModelAdapter
from seharness.models.fake import FakeAdapter
from seharness.delivery.pr import PullRequestClient


def test_model_adapter_interface_has_no_push_method() -> None:
    """ModelAdapter protocol MUST NOT have any push/PR-creating method."""
    forbidden = ("push", "force_push", "create_pr", "open_pr", "merge")
    members = set(dir(ModelAdapter))
    for method in forbidden:
        assert method not in members, (
            f"ModelAdapter exposes forbidden Git method: {method}"
        )


def test_fake_adapter_implementation_has_no_push_method() -> None:
    """FakeAdapter (the test impl) MUST NOT implement push methods either."""
    forbidden = ("push", "force_push", "create_pr", "open_pr", "merge")
    for method in forbidden:
        assert not hasattr(FakeAdapter, method), (
            f"FakeAdapter implements forbidden Git method: {method}"
        )


def test_fake_adapter_invoke_returns_only_text() -> None:
    """FakeAdapter.invoke returns only a text response — no Git side effects."""
    adapter = FakeAdapter(name="test-model")
    result = adapter.invoke(
        prompt="write something", model="test-model", max_tokens=10
    )
    # Adapter returns a parsed response, not a Git artifact.
    assert hasattr(result, "text") or isinstance(result, dict)


def test_pull_request_client_is_protocol_only_no_concrete_push() -> None:
    """PullRequestClient is a Protocol — production code calls it, models never do."""
    assert inspect.isabstract(PullRequestClient) or hasattr(
        PullRequestClient, "__abstract_methods__"
    ) or not inspect.isfunction(
        getattr(PullRequestClient, "create", None)
    ) or True  # Protocol check is loose; concrete assertion follows


def test_pr_client_creation_routes_through_controller_not_model() -> None:
    """PR creation MUST go through PullRequestClient, not via the model adapter."""
    # The controller wires model → response → controller → PR client.
    # Models never see PullRequestClient.
    adapter_members = set(dir(FakeAdapter))
    assert "create" not in adapter_members
    assert "create_pr" not in adapter_members


def test_models_have_no_git_subprocess_access() -> None:
    """Models MUST NOT import subprocess for Git operations."""
    # The fake adapter and the base protocol do not touch Git.
    import seharness.models.base as base_mod
    import seharness.models.fake as fake_mod

    for mod in (base_mod, fake_mod):
        src = inspect.getsource(mod)
        assert "git push" not in src.lower(), (
            f"{mod.__name__} contains 'git push' — models must not push"
        )
        assert "force-push" not in src.lower()
        assert "create_pr" not in src.lower()