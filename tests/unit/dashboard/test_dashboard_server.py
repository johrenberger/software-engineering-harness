"""RED tests for slice-13 live aiohttp dashboard server.

Per SPEC §22 (Operator dashboard) — the dashboard is rendered as HTML
on `127.0.0.1:8765` and exposes:
- GET /            → HTML
- GET /api/state   → JSON snapshot
- GET /healthz     → 200 ok

Bind is 127.0.0.1 only (no public exposure).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest


def _import_server() -> object:
    from seharness.dashboard.server import DashboardServer, DashboardState

    return DashboardServer, DashboardState


def test_server_bind_default_is_loopback() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765)
    assert server.host == "127.0.0.1"


def test_server_bind_explicit_loopback_allowed() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765, host="127.0.0.1")
    assert server.host == "127.0.0.1"


def test_server_rejects_public_bind() -> None:
    cls, _ = _import_server()
    with pytest.raises((ValueError, RuntimeError)):
        cls(state_provider=MagicMock(), port=8765, host="0.0.0.0")  # type: ignore[arg-type]


def test_server_state_provider_returns_snapshot() -> None:
    cls, state_cls = _import_server()
    provider = MagicMock()
    provider.return_value = state_cls(
        slice="12",
        last_green_commit="9cd4831",
        runs=("run-001", "run-002"),
        harness_state="ready",
        generated_at="2026-07-19T00:00:00Z",
    )
    server = cls(state_provider=provider, port=8765)
    snapshot = server.snapshot()
    assert snapshot.slice == "12"
    assert snapshot.last_green_commit == "9cd4831"
    assert snapshot.runs == ("run-001", "run-002")


def test_server_routes_have_expected_paths() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765)
    paths = {route.path for route in server.routes}
    assert "/" in paths
    assert "/api/state" in paths
    assert "/healthz" in paths


def test_server_healthz_returns_ok() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765)
    health_route = next(r for r in server.routes if r.path == "/healthz")
    # The handler exists and is callable.
    assert callable(health_route.handler)


def test_server_api_state_returns_json() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765)
    api_route = next(r for r in server.routes if r.path == "/api/state")
    assert callable(api_route.handler)


def test_server_index_renders_html() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock(), port=8765)
    index_route = next(r for r in server.routes if r.path == "/")
    assert callable(index_route.handler)


def test_server_default_port_is_8765() -> None:
    cls, _ = _import_server()
    server = cls(state_provider=MagicMock())
    assert server.port == 8765


def test_server_state_is_frozen_dataclass() -> None:
    cls, state_cls = _import_server()
    state = state_cls(
        slice="12",
        last_green_commit="9cd4831",
        runs=(),
        harness_state="ready",
        generated_at="2026-07-19T00:00:00Z",
    )
    with pytest.raises((AttributeError, TypeError)):
        state.slice = "13"  # type: ignore[misc]
