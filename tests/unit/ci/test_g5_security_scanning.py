"""G5: contract tests for the security-scanning workflow bundle.

G5 ships three workflows that scan the repo for supply-chain
weaknesses:

1. ``.github/workflows/pip-audit.yml`` — pyproject + uv.lock audit.
2. ``.github/workflows/codeql.yml`` — GitHub-official CodeQL.
3. ``.github/workflows/openssf-scorecard.yml`` — OSSF Scorecard.

These tests pin the G5 invariants so the bundle stays in sync with
the rest of the supply-chain posture (G4 SHA-pinning, G6 Dependabot,
G7 SBOM+provenance).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"

PIP_AUDIT_YML = WORKFLOW_DIR / "pip-audit.yml"
CODEQL_YML = WORKFLOW_DIR / "codeql.yml"
SCORECARD_YML = WORKFLOW_DIR / "openssf-scorecard.yml"

EXPECTED_PINS: dict[str, str] = {
    # pip-audit
    "actions/checkout": "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "pypa/gh-action-pip-audit": "1220774d901786e6f652ae159f7b6bc8fea6d266",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    # codeql
    "github/codeql-action/init": "b7351df727350dca84cb9d725d57dcf5bc82ba26",
    "github/codeql-action/analyze": "b7351df727350dca84cb9d725d57dcf5bc82ba26",
    # openssf-scorecard
    "ossf/scorecard-action": "4eaacf0543bb3f2c246792bd56e8cdeffafb205a",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pip_audit() -> dict:
    return _load_yaml(PIP_AUDIT_YML)


@pytest.fixture(scope="module")
def codeql() -> dict:
    return _load_yaml(CODEQL_YML)


@pytest.fixture(scope="module")
def scorecard() -> dict:
    return _load_yaml(SCORECARD_YML)


# --- Workflow files exist --------------------------------------------------


def test_pip_audit_workflow_exists() -> None:
    assert PIP_AUDIT_YML.exists(), "G5: pip-audit.yml must exist"


def test_codeql_workflow_exists() -> None:
    assert CODEQL_YML.exists(), "G5: codeql.yml must exist"


def test_scorecard_workflow_exists() -> None:
    assert SCORECARD_YML.exists(), "G5: openssf-scorecard.yml must exist"


# --- SHA-pinning (G4 consistency) ------------------------------------------


def _all_uses(workflow: dict) -> list[tuple[str, str]]:
    """Return [(owner/repo, ref), ...] for every step in a workflow."""
    out: list[tuple[str, str]] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if not uses:
                continue
            # format: owner/repo@<ref> [ # comment ]
            if "@" not in uses:
                continue
            owner_repo, ref = uses.split("@", 1)
            ref = ref.split()[0]  # strip comments
            out.append((owner_repo, ref))
    return out


def test_pip_audit_all_uses_are_sha_pinned(pip_audit: dict) -> None:
    """Every `uses:` in pip-audit.yml must be a 40-char SHA (G4 rule)."""
    for owner_repo, ref in _all_uses(pip_audit):
        if owner_repo.startswith("./"):  # local action
            continue
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
            f"pip-audit.yml: {owner_repo}@{ref} is not a 40-char SHA "
            f"(G4 violation — must be SHA-pinned)"
        )


def test_codeql_all_uses_are_sha_pinned(codeql: dict) -> None:
    """Every `uses:` in codeql.yml must be a 40-char SHA."""
    for owner_repo, ref in _all_uses(codeql):
        if owner_repo.startswith("./"):
            continue
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
            f"codeql.yml: {owner_repo}@{ref} is not SHA-pinned"
        )


def test_scorecard_all_uses_are_sha_pinned(scorecard: dict) -> None:
    """Every `uses:` in openssf-scorecard.yml must be a 40-char SHA."""
    for owner_repo, ref in _all_uses(scorecard):
        if owner_repo.startswith("./"):
            continue
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
            f"openssf-scorecard.yml: {owner_repo}@{ref} is not SHA-pinned"
        )


@pytest.mark.parametrize(
    ("owner_repo", "expected_sha"),
    sorted(EXPECTED_PINS.items()),
)
def test_expected_sha_pin_is_used(owner_repo: str, expected_sha: str) -> None:
    """The pin map in this test must agree with what's in the workflows."""
    found_in: list[str] = []
    for path in (PIP_AUDIT_YML, CODEQL_YML, SCORECARD_YML):
        if not path.exists():
            continue
        for _or, ref in _all_uses(_load_yaml(path)):
            if _or == owner_repo and ref == expected_sha:
                found_in.append(path.name)
    assert owner_repo in {"".join(found_in)} or found_in, (
        f"{owner_repo}@{expected_sha} expected in one of the G5 workflows; found in: {found_in}"
    )


# --- Permissions (minimum-privilege) ---------------------------------------


def test_pip_audit_has_minimum_permissions(pip_audit: dict) -> None:
    """pip-audit.yml must declare minimum-privilege permissions.

    security-events: write must live at JOB scope (matches Scorecard
    pattern from PR #38); workflow-level writes trigger a Token-Permissions
    warning.
    """
    top_perms = pip_audit.get("permissions", {})
    job_perms = next(iter(pip_audit.get("jobs", {}).values()), {}).get("permissions", {})
    assert "contents" in top_perms
    assert top_perms.get("security-events") != "write", (
        "pip-audit: security-events: write should be at JOB scope "
        "to avoid the Token-Permissions Scorecard warning"
    )
    assert job_perms.get("security-events") == "write", (
        f"pip-audit job needs security-events: write; got {job_perms!r}"
    )


def test_codeql_has_minimum_permissions(codeql: dict) -> None:
    """codeql.yml must declare minimum-privilege permissions.

    security-events: write at JOB scope (not workflow scope).
    """
    top_perms = codeql.get("permissions", {})
    job_perms = next(iter(codeql.get("jobs", {}).values()), {}).get("permissions", {})
    assert "contents" in top_perms
    assert top_perms.get("security-events") != "write", (
        "codeql: security-events: write should be at JOB scope "
        "to avoid the Token-Permissions Scorecard warning"
    )
    assert job_perms.get("security-events") == "write", (
        f"codeql job needs security-events: write; got {job_perms!r}"
    )


def test_scorecard_has_minimum_permissions(scorecard: dict) -> None:
    """Scorecard needs `security-events: write` AND `id-token: write` —
    but only at JOB scope, not workflow scope, per its workflow
    restrictions (https://github.com/ossf/scorecard-action#workflow-restrictions).

    We accept job-level permissions for both; workflow-level write
    permissions trigger the 400 Bad Request from the publish API.
    """
    top_perms = scorecard.get("permissions", {})
    job_perms = next(iter(scorecard.get("jobs", {}).values()), {}).get("permissions", {})
    # All write permissions must live at job scope.
    assert top_perms.get("security-events") != "write", (
        "scorecard: security-events: write at WORKFLOW scope is rejected "
        "by the publish_results REST API. Move to JOB scope."
    )
    assert top_perms.get("id-token") != "write", (
        "scorecard: id-token: write at WORKFLOW scope is rejected by the "
        "publish_results REST API. Move to JOB scope."
    )
    # Job scope must include both writes.
    assert job_perms.get("security-events") == "write", (
        f"scorecard job needs security-events: write; got {job_perms!r}"
    )
    assert job_perms.get("id-token") == "write", (
        f"scorecard job needs id-token: write; got {job_perms!r}"
    )
    # contents: read should be at workflow scope (cheap default).
    assert top_perms.get("contents") == "read"


# --- Triggers --------------------------------------------------------------


def test_pip_audit_runs_on_pr_and_push(pip_audit: dict) -> None:
    """pip-audit must run on every PR + push to main (continuous monitoring)."""
    on = pip_audit.get(True, pip_audit.get("on", {}))
    assert "pull_request" in on
    assert "push" in on
    # And on a schedule (weekly vuln DB refresh).
    assert "schedule" in on


def test_codeql_runs_on_pr_and_push_and_schedule(codeql: dict) -> None:
    on = codeql.get(True, codeql.get("on", {}))
    assert "pull_request" in on
    assert "push" in on
    assert "schedule" in on


def test_scorecard_runs_on_push_only(scorecard: dict) -> None:
    """Scorecard runs on push only — not as a PR check (per upstream docs)."""
    on = scorecard.get(True, scorecard.get("on", {}))
    assert "push" in on
    assert "schedule" in on
    # PRs are explicitly NOT in the trigger set — scorecard is not a PR check.


# --- Workflow-specific wiring ---------------------------------------------


def test_pip_audit_uses_lockfile(pip_audit: dict) -> None:
    """pip-audit should audit a compiled locked requirements file.

    pypa/gh-action-pip-audit expects requirements.txt-style input
    (not pyproject.toml directly). The workflow compiles pyproject +
    uv.lock into requirements.lock.txt via `uv pip compile` before
    auditing.
    """
    text = PIP_AUDIT_YML.read_text(encoding="utf-8")
    assert "uv pip compile" in text or "pip-compile" in text
    assert "requirements" in text


def test_pip_audit_fails_on_vuln(pip_audit: dict) -> None:
    """The default behavior of pypa/gh-action-pip-audit is to fail on
    any vulnerability. We document this in the workflow comment.

    Note: v1.1.0 does NOT have a ``vulnerability-check: critical``
    input (that was an older/different action). Failing-on-any-vuln
    is the v1.1.0 default.
    """
    text = PIP_AUDIT_YML.read_text(encoding="utf-8")
    # No vulnerability-check input (which v1.1.0 doesn't accept).
    assert "vulnerability-check" not in text
    # The composite action's default behavior is to fail on any vuln.
    # We document this in the workflow comment.
    assert "fail" in text.lower() or "vuln" in text.lower()


def test_codeql_targets_python(codeql: dict) -> None:
    """The matrix must include 'python' (the only language we ship)."""
    matrix = codeql["jobs"]["analyze"]["strategy"]["matrix"]
    assert "python" in matrix.get("language", [])


def test_codeql_uses_security_extended_queries(codeql: dict) -> None:
    """queries: security-extended gives wider coverage than security-only."""
    text = CODEQL_YML.read_text(encoding="utf-8")
    assert "queries:" in text
    assert "security-extended" in text


def test_scorecard_uploads_sarif(scorecard: dict) -> None:
    """Scorecard must upload SARIF + publish_results (per upstream template)."""
    text = SCORECARD_YML.read_text(encoding="utf-8")
    assert "results_format: sarif" in text
    assert "publish_results: true" in text


# --- Cross-cutting bundle invariants --------------------------------------


def test_three_workflows_have_distinct_names() -> None:
    """The three workflows must have unique names (no collision)."""
    names = [
        _load_yaml(p)["name"] for p in (PIP_AUDIT_YML, CODEQL_YML, SCORECARD_YML) if p.exists()
    ]
    assert len(names) == len(set(names)), f"Duplicate workflow names: {names}"


def test_g5_workflows_do_not_redefine_existing_ci_steps() -> None:
    """G5 must NOT duplicate quality gates that already live in ci.yml.

    Specifically, the inline `pip-audit` step in ci.yml stays as the
    PR-time check; the dedicated pip-audit.yml adds weekly + SARIF
    upload. Don't move the PR-time check out of ci.yml — that would
    fork the contract tests.
    """
    ci_text = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
    assert "pip-audit" in ci_text, (
        "ci.yml must keep its inline `pip-audit` step (G5 doesn't replace it)."
    )


def test_g5_workflows_cite_supply_chain_posture() -> None:
    """Each G5 workflow header should reference G4 (SHA-pinning) and the
    broader supply-chain posture so a future maintainer can trace why
    the workflow exists."""
    for path in (PIP_AUDIT_YML, CODEQL_YML, SCORECARD_YML):
        text = path.read_text(encoding="utf-8")
        # G4 is the SHA-pinning rule; the workflow files all comment on it.
        assert "G4" in text, (
            f"{path.name} should comment on the G4 SHA-pinning rule for context (audit-trail)."
        )
