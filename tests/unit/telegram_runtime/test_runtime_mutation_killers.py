"""RED tests — slice-13 mutation killers.

Pydantic frozen models + Protocol shapes that mutmut 2.5.1 cannot
traverse by AST-RHS alone. These tests pin behaviors that mutants would
silently break.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from typing import Any

import pytest


def test_bot_runtime_init_validates_token_type() -> None:
    """`bot_token` must be a non-empty string."""
    from seharness.telegram_runtime.bot_runtime import TelegramBotRuntime

    with pytest.raises((TypeError, ValueError)):
        TelegramBotRuntime(bot_token=None, service=object())  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        TelegramBotRuntime(bot_token="", service=object())  # type: ignore[arg-type]


def test_bot_runtime_chat_ids_are_tuple_of_int() -> None:
    from seharness.telegram_runtime.bot_runtime import TelegramBotRuntime

    rt = TelegramBotRuntime(bot_token="123456:abcdefghijklmnopqrstuvwx", service=object())
    assert isinstance(rt.allowed_chat_ids, tuple)
    for cid in rt.allowed_chat_ids:
        assert isinstance(cid, int)


def test_bot_runtime_no_merge_methods() -> None:
    """Auto-merge prevention layer 5: TelegramBotRuntime has no merge methods."""
    from seharness.telegram_runtime.bot_runtime import TelegramBotRuntime

    rt = TelegramBotRuntime(bot_token="123456:abcdefghijklmnopqrstuvwx", service=object())
    forbidden = ("merge", "auto_merge", "merge_pr", "gh_pr_merge", "merge_pull_request")
    for name in forbidden:
        assert not hasattr(rt, name), f"TelegramBotRuntime.{name} must not exist"


def test_command_dispatcher_no_merge_methods() -> None:
    from seharness.telegram_runtime.command_handlers import CommandDispatcher

    dispatcher = CommandDispatcher(service=object())
    forbidden = ("merge", "auto_merge", "merge_pr", "gh_pr_merge", "merge_pull_request")
    for name in forbidden:
        assert not hasattr(dispatcher, name), f"CommandDispatcher.{name} must not exist"


def test_dashboard_state_frozen() -> None:
    from seharness.dashboard.server import DashboardState

    state = DashboardState(
        slice="12",
        last_green_commit="9cd4831",
        runs=(),
        harness_state="ready",
        generated_at="2026-07-19T00:00:00Z",
    )
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        state.slice = "13"  # type: ignore[misc]


def test_dashboard_state_field_types_pinned() -> None:
    """The dashboard snapshot field types are pinned by this test."""
    from seharness.dashboard.server import DashboardState

    state = DashboardState(
        slice="12",
        last_green_commit="9cd4831",
        runs=("run-001",),
        harness_state="ready",
        generated_at="2026-07-19T00:00:00Z",
    )
    assert isinstance(state.slice, str)
    assert isinstance(state.last_green_commit, str)
    assert isinstance(state.runs, tuple)
    assert isinstance(state.harness_state, str)
    assert isinstance(state.generated_at, str)


def test_dashboard_server_rejects_public_bind_explicitly() -> None:
    """Server must reject non-loopback bind (security: SPEC §22)."""
    from seharness.dashboard.server import DashboardServer

    with pytest.raises((ValueError, RuntimeError)):
        DashboardServer(state_provider=lambda: None, host="0.0.0.0")  # type: ignore[arg-type]
    with pytest.raises((ValueError, RuntimeError)):
        DashboardServer(state_provider=lambda: None, host="::")  # type: ignore[arg-type]


def test_skill_registry_names_returns_tuple_immutable() -> None:
    from seharness.skills.registry import SkillRegistry

    reg = SkillRegistry.default()
    names = reg.names()
    # tuple, not list — immutable.
    assert isinstance(names, tuple)
    # Attempt to mutate → raises.
    with pytest.raises((TypeError, AttributeError)):
        names[0] = "tampered"  # type: ignore[index]


def test_pipeline_result_terminal_state_immutable_string() -> None:
    """PipelineResult.terminal_state is a frozen string."""
    from seharness.pipeline.vertical_slice import PipelineResult

    result = PipelineResult(
        run_id="run-001",
        terminal_state="completed",
        events=(),
    )
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        result.terminal_state = "running"  # type: ignore[misc]
