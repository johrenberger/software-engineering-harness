# Cluster M3-4 fixture: /health

Minimal FastAPI repo used by the M3-4 offline vertical acceptance test
(`tests/e2e/test_m3_offline_vertical.py`).

**Before the run** the app has no `/health` endpoint. **After the run**
the app must implement `/health` returning `200 {"status": "ok"}`.

The fixture is intentionally tiny so the orchestrator's planning /
implementation / review assertions are deterministic and the diff
fits inside the corrective doc's allowed-path budget.

## Layout

```
health_fixture_repo/
├── pyproject.toml            # so CommandResolver picks pytest
├── main.py                   # FastAPI app, NO /health route
├── tests/
│   ├── __init__.py
│   └── test_health.py        # targeted test, fails before, passes after
└── README.md
```

## Bootstrap

The offline vertical test copies this directory to `tmp_path`, then
runs `git init` + initial commit so `RunContext.base_git_sha`
captures a real SHA. The bootstrap helper lives in
`tests/e2e/_bootstrap.py::bootstrap_health_fixture_repo(tmp_path)`.

## M3-5 swap

M3-5 (live MiniMax-M3 vertical acceptance) does NOT change this
fixture. It only swaps the recordings in
`tests/fixtures/minimax_m3_recordings/` for live ones (or adds a
sibling directory of live recordings); the fixture repo is the
same.
