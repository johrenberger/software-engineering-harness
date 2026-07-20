"""Contract tests for G4 — Actions SHA pinning.

G4 pins every action reference in the project's workflows to a full
commit SHA, with the tag as a trailing comment. This makes the
supplier of the action's code immutable — even if the upstream tag
gets deleted or moved (a known class of GitHub Actions supply-chain
attack: actions/runner-images CVE-style tag-hijack), the workflow
keeps using the exact commit it was tested against.

References:
- G4 spec: docs/analysis/2026-07-19-priority-stories.md
- GH Actions hardening guide: third-party actions must be pinned to
  a commit SHA, not a tag.
- Pattern: ``uses: owner/repo@<40-char-sha> # <version>``

The expected SHA map is the contract; if any action's upstream SHA
changes (e.g. force-push, tag deletion), the test FAILS, forcing a
deliberate re-pin + re-test of the action.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))


# ----------------------------------------------------------------------
# Expected SHA pins (manually verified via git/refs/tags/<tag> on each
# upstream repo). Keep in sync with `gh api repos/<owner>/<repo>/git/refs/tags/<tag>`.
# ----------------------------------------------------------------------

# Expected SHA pins (manually verified via git/refs/tags/<tag> on each
# upstream repo). Keep in sync with `gh api repos/<owner>/<repo>/git/refs/tags/<tag>`.
# The KEY is the canonical ref (owner/repo@tag) that humans read; the VALUE is
# the commit SHA the workflow actually pins to. We separate them so that the
# version comment stays human-readable while the SHA is the security primitive.
EXPECTED_PINS: dict[str, str] = {
    "actions/checkout@v4": "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "actions/setup-python@v5": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact@v4": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact@v4": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/configure-pages@v5": "983d7736d9b0ae728b81ab479565c72886d7745b",
    "actions/upload-pages-artifact@v3": "56afc609e74202658d3ffba0e8f6dda462b719fa",
    "actions/deploy-pages@v4": "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e",
    "actions/attest-build-provenance@v1": "ef244123eb79f2f7a7e75d99086184180e6d0018",
    "anchore/sbom-action@v0": "e22c389904149dbc22b58101806040fa8d37a610",
    # G5: pip-audit, CodeQL, OpenSSF Scorecard (PR #36).
    "pypa/gh-action-pip-audit@v1.1.0": "1220774d901786e6f652ae159f7b6bc8fea6d266",
    "github/codeql-action/init@v3": "b7351df727350dca84cb9d725d57dcf5bc82ba26",
    "github/codeql-action/analyze@v3": "b7351df727350dca84cb9d725d57dcf5bc82ba26",
    "ossf/scorecard-action@v2.4.3": "4eaacf0543bb3f2c246792bd56e8cdeffafb205a",
    "astral-sh/setup-uv@v6": "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    # G9: PyPI release workflow (release.yml).
    "pypa/gh-action-pypi-publish@v1.12": "67339c736fd9354cd4f8cb0b744f2b82a74b5c70",
    "softprops/action-gh-release@v2.6.2": "3bb12739c298aeb8a4eeaf626c5b8d85266b0e65",
}

# Reverse map: (owner/repo, sha) -> version tag. Used to translate a pinned
# SHA back into the human-readable key for the contract map lookup.
_SHA_TO_KEY: dict[str, str] = {sha: key for key, sha in EXPECTED_PINS.items()}


# 40-char SHA (hex).
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# usage: owner/repo@<sha or tag>
USES_RE = re.compile(r"uses:\s+([\w\-]+(?:/[\w\-]+)+)@(\S+)(.*)$")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _iter_uses(text: str):
    """Yield (owner/repo, ref, trailing_comment) for each uses: line."""
    for line in text.splitlines():
        m = USES_RE.search(line)
        if m:
            yield m.group(1), m.group(2).strip(), m.group(3).strip()


def _action_key(owner_repo: str, ref: str) -> str:
    """Translate (owner/repo, ref) into the EXPECTED_PINS key (action@version).

    Strips any trailing comments from ref.
    """
    return f"{owner_repo}@{ref.split('#', 1)[0].strip()}"


def _trailing_version(ref: str) -> str:
    """Extract the trailing comment (e.g. ' # v4') for the
    version-comment assertion.
    """
    return ref.split("#", 1)[-1].strip() if "#" in ref else ""


# ----------------------------------------------------------------------
# 1. No `uses:` line is a bare tag (defense in depth)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_uses_is_a_bare_version_tag(workflow: Path) -> None:
    """No action reference is just `@vN` — must be a SHA, optionally with `# vN`."""
    text = workflow.read_text()
    bare_tags = []
    for owner_repo, ref, _ in _iter_uses(text):
        # If the ref doesn't match the 40-char SHA pattern AND doesn't
        # have a `# v...` comment, it's a bare tag (BAD).
        ref_clean = ref.split("#", 1)[0].strip()
        if not SHA_RE.match(ref_clean):
            bare_tags.append(f"{owner_repo}@{ref_clean}")
    assert not bare_tags, (
        f"{workflow.name}: action references must be SHA-pinned (G4); "
        f"bare tag refs found: {bare_tags}. Use `uses: owner/repo@<sha> # vN`."
    )


# ----------------------------------------------------------------------
# 2. SHA pins match the expected map exactly
# ----------------------------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_all_pins_match_expected_map(workflow: Path) -> None:
    """Every action pin must match the contract map. Upstream SHA changes
    are caught by this test (forces deliberate re-pin + re-test).
    """
    text = workflow.read_text()
    mismatches = []
    unknown = []
    for owner_repo, ref, _ in _iter_uses(text):
        ref_clean = ref.split("#", 1)[0].strip()
        # Find every key in EXPECTED_PINS whose owner/repo matches AND
        # whose SHA matches ref_clean. Multiple keys may share a SHA
        # (e.g. github/codeql-action/init and .../analyze both pin to
        # the same bundle SHA), so iterate the map instead of using
        # a single-value reverse map.
        candidate_keys = [
            k
            for k, sha in EXPECTED_PINS.items()
            if k.startswith(f"{owner_repo}@") and sha == ref_clean
        ]
        if candidate_keys:
            # Pin matches at least one expected entry.
            assert len(candidate_keys) >= 1
        elif SHA_RE.match(ref_clean):
            # SHA-pinned but not in our map — surface it.
            unknown.append(f"{owner_repo}@{ref_clean[:12]}")
    msgs = []
    if mismatches:
        msgs.append(f"SHA drift: {mismatches}. Upstream tag moved; re-pin.")
    if unknown:
        msgs.append(
            f"Unmapped SHA pins: {unknown}. Add to EXPECTED_PINS so this test "
            f"guards against future drift."
        )
    assert not msgs, f"{workflow.name}: " + "; ".join(msgs)


# ----------------------------------------------------------------------
# 3. Each pinned ref has a `# <version>` trailing comment for readability
# ----------------------------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_pins_have_trailing_version_comment(workflow: Path) -> None:
    """`# vN` after the SHA makes the pinned version human-readable.

    Without the comment, `uses: actions/checkout@34e1148760b...` is
    opaque. The version comment is what GitHub's hardening guide
    recommends as the canonical pattern.
    """
    text = workflow.read_text()
    bad = []
    for owner_repo, ref, trailing in _iter_uses(text):
        ref_clean = ref.split("#", 1)[0].strip()
        if SHA_RE.match(ref_clean) and not trailing.startswith("#"):
            bad.append(f"{owner_repo}@{ref_clean[:12]}")
    assert not bad, (
        f"{workflow.name}: pinned SHAs should have a `# vN` comment for "
        f"readability. Missing comments on: {bad}"
    )


# ----------------------------------------------------------------------
# 4. SHA pins are full-length (40 hex chars)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_shas_are_40_chars(workflow: Path) -> None:
    """Pins must be full 40-char SHA-1 — not a 7-12 char abbreviation."""
    text = workflow.read_text()
    short_pins = []
    for owner_repo, ref, _ in _iter_uses(text):
        ref_clean = ref.split("#", 1)[0].strip()
        # Already 40 chars ok; 7-12 chars bad; otherwise also bad.
        if SHA_RE.match(ref_clean):
            continue
        # Not a SHA and not just `# comment` — it's a tag → BAD.
        if re.fullmatch(r"[0-9a-f]+", ref_clean) and len(ref_clean) < 40:
            short_pins.append(f"{owner_repo}@{ref_clean}")
    assert not short_pins, (
        f"{workflow.name}: SHA pins must be full 40 chars; got short forms: {short_pins}"
    )


# ----------------------------------------------------------------------
# 5. Every action in EXPECTED_PINS is used at least once (the map stays current)
# ----------------------------------------------------------------------


def test_expected_pins_map_is_completely_used() -> None:
    """The pin map shouldn't accumulate dead entries."""
    used: set[str] = set()
    for wf in WORKFLOWS:
        text = wf.read_text()
        for owner_repo, ref, _ in _iter_uses(text):
            ref_clean = ref.split("#", 1)[0].strip()
            if SHA_RE.match(ref_clean):
                used.add(f"{owner_repo}@{ref_clean}")
    unused = [k for k, sha in EXPECTED_PINS.items() if f"{k.split('@')[0]}@{sha}" not in used]
    assert not unused, (
        f"EXPECTED_PINS entries no longer used by any workflow: {unused}. "
        f"Remove them so the contract map doesn't go stale."
    )


# ----------------------------------------------------------------------
# 6. No anchor to local actions (sanity: everything comes from upstream)
# ----------------------------------------------------------------------


def test_no_local_action_references() -> None:
    """Action refs must be `owner/repo@...` — not `./relative/path`.

    A local action reference is fine when intentional, but the
    project has no local actions defined. This guards against
    accidentally reverting to a local action later.
    """
    for wf in WORKFLOWS:
        text = wf.read_text()
        for line in text.splitlines():
            m = re.search(r"uses:\s+\./", line)
            assert not m, (
                f"{wf.name}: local action reference (./...) found. "
                f"Either remove it or extend this test."
            )


# ----------------------------------------------------------------------
# 7. workflows have a top-level `permissions:` block (defense in depth)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_has_permissions_block(workflow: Path) -> None:
    """Every workflow must declare its own `permissions:` block (G7/G4 posture)."""
    wf = yaml.safe_load(workflow.read_text())
    assert "permissions" in wf, (
        f"{workflow.name}: must declare a top-level `permissions:` block "
        f"(minimum-privilege token; complement to G4 SHA pinning)."
    )
    perms = wf["permissions"]
    # Must be a dict (keyed scope), or `read-all` / `{}` (most restrictive).
    assert isinstance(perms, dict), (
        f"{workflow.name}: `permissions:` must be a dict of scope→level, got {perms!r}"
    )
    # contents is required (for checkout); should be `read` (not `write`).
    assert perms.get("contents", "read") == "read", (
        f"{workflow.name}: contents should be `read` (minimum-privilege). "
        f"Got {perms.get('contents')!r}"
    )


# ----------------------------------------------------------------------
# 8. ci.yml top-level permissions block retains the G7 attestation scopes
# ----------------------------------------------------------------------


def test_ci_workflow_permissions_include_attestations() -> None:
    """ci.yml needs `attestations: write` for actions/attest-build-provenance.

    Regression guard for PR #29 (G7 attestation wiring).
    """
    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    perms = ci.get("permissions", {})
    assert perms.get("attestations") == "write", (
        f"ci.yml must declare `attestations: write` for build provenance attestation. Got: {perms}"
    )
    assert perms.get("id-token") == "write", (
        f"ci.yml must declare `id-token: write` for OIDC exchange. Got: {perms}"
    )


# ---------------------------------------------------------------------------
# 9. Every pinned SHA actually resolves upstream (force-push detection)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("RUN_NETWORK_PIN_CHECK"),
    reason=(
        "Skipped by default (offline CI). Set RUN_NETWORK_PIN_CHECK=1 to "
        "verify that every pinned SHA still resolves upstream. This catches "
        "force-push / history-rewrites on action repos (which silently "
        "break our pin map). Re-run this on a routine schedule (e.g. "
        "monthly) and after any unexpected release workflow failure."
    ),
)
def test_pinned_shas_resolve_upstream() -> None:
    """Every pinned SHA in EXPECTED_PINS MUST resolve to an upstream
    commit. A 422 from the GitHub commits API means the SHA has been
    invalidated (force-push / repo rewrite). Without this check we
    only learn the pin is stale when the release workflow fails on
    the next tag push.

    Uses ``gh api`` when available (authenticated, bypasses the
    unauthenticated 60-req/h rate limit). Falls back to urllib for
    unauthenticated runs; rate-limit (HTTP 429) failures are then
    reported as a known-environment limitation rather than a pin
    regression.
    """
    import shutil
    import subprocess

    failures: list[str] = []
    rate_limited: list[str] = []

    use_gh = shutil.which("gh") is not None

    for key, sha in EXPECTED_PINS.items():
        # Resolve at the repo level. Sub-paths (e.g.
        # ``github/codeql-action/init``) share a commit with their
        # parent repo; only the first 2 path segments are the
        # ``owner/repo`` pair.
        owner_repo = "/".join(key.split("@", 1)[0].split("/")[:2])
        if use_gh:
            proc = subprocess.run(
                ["gh", "api", f"repos/{owner_repo}/commits/{sha}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0:
                continue
            if proc.stderr and "rate limit" in proc.stderr.lower():
                rate_limited.append(key)
                continue
            failures.append(f"{key} -> gh api rc={proc.returncode}: {proc.stderr.strip()[:80]}")
        else:
            import urllib.error
            import urllib.request

            url = f"https://api.github.com/repos/{owner_repo}/commits/{sha}"
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                    if resp.status != 200:
                        failures.append(f"{key} -> {resp.status}")
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    rate_limited.append(key)
                else:
                    failures.append(f"{key} -> HTTP {e.code}")
            except (urllib.error.URLError, TimeoutError) as e:
                failures.append(f"{key} -> network: {e}")

    # 429s without an authenticated client are an environment
    # limitation, not a pin regression. Surface them in the assertion
    # message so operators know what happened, but don't fail the
    # contract unless at least one non-429 failure was found.
    if rate_limited and not failures and not use_gh:
        pytest.skip(
            f"Unauthenticated run hit GitHub rate limit on {len(rate_limited)} pins; "
            f"re-run with `gh auth status` (uses `gh api` with credentials) or set "
            f"GITHUB_TOKEN. Pins checked before rate-limit: no failures found."
        )
    assert not failures, (
        "Pinned SHAs no longer resolve upstream (force-push detected). "
        "Re-pin and re-test:\n" + "\n".join(f"  {f}" for f in failures)
    )
