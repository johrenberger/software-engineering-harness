"""Cluster M3-4 fixture: targeted test for the missing ``/health`` endpoint.

This test currently FAILS because the fixture's ``main.py`` has no
``/health`` route. After the M3-4 orchestrator run applies the
production patch, this test must PASS.
"""

from fastapi.testclient import TestClient
from main import app


def test_health_returns_ok() -> None:
    """The orchestrator must add a ``/health`` route returning 200 + ok."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"status": "ok"}
