"""Cluster M3-4 fixture: minimal FastAPI app **without** a /health endpoint.

The M3-4 offline vertical acceptance asserts that:

1. ``/health`` is absent BEFORE the run (this file has no
   health route definition).
2. After the orchestrator runs with the M3 composition, ``/health``
   is present, returns 200, and returns ``{"status": "ok"}``.

The fixture is committed to ``tests/fixtures/health_fixture_repo/``
and copied to ``tmp_path`` (then ``git init``-ed) by the offline
vertical acceptance test. Keep this file minimal so the orchestrator's
discovery / planning / review assertions are deterministic.
"""

from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root() -> dict[str, str]:
    return {"msg": "fixture-repo"}


@app.get("/items/{item_id}")
def read_item(item_id: int) -> dict[str, object]:
    return {"item_id": item_id}
