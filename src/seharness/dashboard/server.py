"""Live aiohttp dashboard server.

Per SPEC §22 (Operator dashboard): HTTP server on 127.0.0.1:8765
exposing:
- GET /            → HTML (via DashboardRenderer)
- GET /api/state   → JSON snapshot
- GET /healthz     → 200 ok

Bind is 127.0.0.1 only — public bind is rejected (security).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from seharness.dashboard.renderer import (
    DashboardRenderer,
    DashboardSnapshot,
    GitCommit,
)


@dataclass(frozen=True)
class DashboardState:
    """Frozen snapshot served by the dashboard."""

    slice: str
    last_green_commit: str
    runs: tuple[str, ...]
    harness_state: str
    generated_at: str


@dataclass
class _Route:
    """Internal record of an HTTP route (avoids touching aiohttp internals)."""

    path: str
    method: str
    handler: Callable[..., Any]


class DashboardServer:
    """Bind 127.0.0.1:8765 by default; rejects non-loopback bind."""

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 8765
    ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    def __init__(
        self,
        state_provider: Callable[[], DashboardState | DashboardSnapshot],
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        if host not in self.ALLOWED_HOSTS:
            raise ValueError(
                f"DashboardServer must bind to a loopback address; "
                f"got {host!r}. Allowed: {sorted(self.ALLOWED_HOSTS)}"
            )
        self.host = host
        self.port = port
        self._state_provider = state_provider
        self._routes: tuple[_Route, ...] = (
            _Route("/", "GET", self._handle_index),
            _Route("/api/state", "GET", self._handle_api_state),
            _Route("/healthz", "GET", self._handle_healthz),
        )

    @property
    def routes(self) -> tuple[_Route, ...]:
        return self._routes

    def snapshot(self) -> DashboardSnapshot:
        """Render the current snapshot via the state provider."""
        from seharness.controller.run_ledger import RunRecord, RunState

        state = self._state_provider()
        if isinstance(state, DashboardState):
            latest = None
            if state.runs:
                latest = RunRecord(
                    run_id=state.runs[0],
                    state=RunState.COMPLETE,
                    repository="unknown",
                    started_at=state.generated_at,
                )
            return DashboardSnapshot(
                harness_version="0.1.0",
                current_slice=state.slice,
                current_slice_name=f"slice {state.slice}",
                last_green_commit=GitCommit(
                    sha=state.last_green_commit,
                    message="last green",
                    committed_at=datetime.now(tz=UTC),
                ),
                latest_run=latest,
                generated_at=datetime.now(tz=UTC),
            )
        return state

    # --- handlers -------------------------------------------------------

    async def _handle_index(self, _request: web.Request) -> web.Response:
        snap = self.snapshot()
        body = DashboardRenderer().render(snap)
        return web.Response(text=body, content_type="text/html")

    async def _handle_api_state(self, _request: web.Request) -> web.Response:
        snap = self.snapshot()
        return web.Response(
            text=json.dumps(
                {
                    "slice": snap.current_slice,
                    "last_green": snap.last_green_commit.sha if snap.last_green_commit else None,
                    "runs": [r.run_id for r in (snap.latest_run,)] if snap.latest_run else [],
                    "harness_state": "ready",
                    "generated_at": snap.generated_at.isoformat(),
                }
            ),
            content_type="application/json",
        )

    async def _handle_healthz(self, _request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    def run(self) -> None:
        """Start the dashboard server (blocks until SIGINT)."""
        app = web.Application()
        for route in self._routes:
            app.router.add_route(route.method, route.path, route.handler)
        web.run_app(app, host=self.host, port=self.port)


def cli() -> int:
    """Console-script entry point for the dashboard server."""
    from seharness.controller.config import ApplicationServiceFactory

    factory = ApplicationServiceFactory.default()
    service = factory.build()

    def state_provider() -> DashboardState:
        runs_obj = service.runs() if hasattr(service, "runs") else ()
        runs: tuple[str, ...] = tuple(runs_obj) if isinstance(runs_obj, (list, tuple)) else ()
        slice_no = "?"
        last_green = "?"
        if runs:
            try:
                status_data = service.status(runs[0])
            except Exception:
                status_data = None
            if isinstance(status_data, dict):
                slice_no = str(status_data.get("slice", "?"))
                last_green = str(status_data.get("last_green", "?"))
        return DashboardState(
            slice=slice_no,
            last_green_commit=last_green,
            runs=runs,
            harness_state="ready",
            generated_at="2026-07-19T00:00:00Z",
        )

    server = DashboardServer(state_provider=state_provider)
    server.run()
    return 0
