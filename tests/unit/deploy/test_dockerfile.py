"""RED tests for slice-13 Dockerfile and docker-compose.yml.

Per SPEC §23 Part B bullet 6-7:
- python:3.13-slim base
- non-root user
- HEALTHCHECK
- Image < 200MB
- docker-compose with harness-bot + harness-dashboard services
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _dockerfile() -> str:
    return (ROOT / "docker" / "Dockerfile").read_text()


def _compose() -> str:
    return (ROOT / "docker" / "docker-compose.yml").read_text()


def _workflows() -> Path:
    return ROOT / ".github" / "workflows"


def test_dockerfile_exists() -> None:
    assert (ROOT / "docker" / "Dockerfile").exists()


def test_dockerfile_uses_python_313_slim() -> None:
    assert "python:3.13-slim" in _dockerfile()


def test_dockerfile_runs_as_non_root() -> None:
    df = _dockerfile()
    assert re.search(r"^\s*USER\s+\S+", df, re.MULTILINE), "missing USER directive"


def test_dockerfile_has_healthcheck() -> None:
    assert "HEALTHCHECK" in _dockerfile()


def test_dockerfile_exposes_dashboard_port() -> None:
    """The dashboard is reachable inside the container on 8765."""
    assert "EXPOSE" in _dockerfile()
    assert "8765" in _dockerfile()


def test_dockerfile_sets_workdir() -> None:
    assert re.search(r"^\s*WORKDIR\s+\S+", _dockerfile(), re.MULTILINE)


def test_dockerfile_cmd_runs_harness() -> None:
    """CMD must invoke the harness runtime (not a shell)."""
    df = _dockerfile()
    assert re.search(r"^\s*CMD\s+", df, re.MULTILINE)
    assert "seharness" in df or "harness" in df.lower()


def test_compose_file_exists() -> None:
    assert (ROOT / "docker" / "docker-compose.yml").exists()


def test_compose_defines_harness_bot_service() -> None:
    compose = _compose()
    assert "harness-bot" in compose


def test_compose_defines_harness_dashboard_service() -> None:
    compose = _compose()
    assert "harness-dashboard" in compose


def test_compose_dashboard_bind_is_loopback_only() -> None:
    """Dashboard port must NOT be exposed on 0.0.0.0 (security: SPEC §22)."""
    compose = _compose()
    # Find dashboard service block.
    block_match = re.search(
        r"harness-dashboard:.*?(?=^  \w|^[a-z])",
        compose,
        re.MULTILINE | re.DOTALL,
    )
    assert block_match is not None, "harness-dashboard service not found"
    block = block_match.group(0)
    got_port = block.split("ports:")[1].split("\n")[0] if "ports:" in block else "no ports"
    assert "127.0.0.1:8765:8765" in block, f"dashboard must bind to 127.0.0.1 only; got: {got_port}"


def test_compose_uses_env_file() -> None:
    compose = _compose()
    assert "env_file" in compose or ".env" in compose


def test_compose_healthchecks_present() -> None:
    compose = _compose()
    assert "healthcheck" in compose.lower()


def test_ci_workflow_exists() -> None:
    assert _workflows().exists()
    files = list(_workflows().glob("*.yml")) + list(_workflows().glob("*.yaml"))
    assert len(files) >= 1


def test_ci_workflow_runs_on_push_to_main_and_prs() -> None:
    ci_yml = _workflows() / "ci.yml"
    assert ci_yml.exists(), f"ci.yml not found under {_workflows()}"
    workflow = ci_yml.read_text()
    assert "pull_request" in workflow
    assert "push" in workflow


def test_ci_workflow_runs_required_gates() -> None:
    """The canonical CI workflow (``ci.yml``) must run the required gates.

    Note: as of G5 (PR #36), there are multiple workflows in
    ``.github/workflows/`` (ci.yml, dashboard.yml, pip-audit.yml,
    codeql.yml, openssf-scorecard.yml). We specifically look at
    ``ci.yml`` because that's where the quality gates live; the
    security-scanning workflows have their own dedicated contract
    tests in ``tests/unit/ci/test_g5_security_scanning.py``.
    """
    ci_yml = _workflows() / "ci.yml"
    assert ci_yml.exists(), "ci.yml must exist (the canonical quality-gate workflow)"
    workflow = ci_yml.read_text(encoding="utf-8")
    for gate in ("ruff", "mypy", "pytest", "bandit"):
        assert gate in workflow, f"{gate} missing from CI workflow"
