# Releasing the software-engineering-harness

This document is the canonical release runbook. Follow it from top to
bottom; every step produces an artifact tracked in the project's
release audit trail.

> **Status:** Automated (cluster G story G9, v0.2.0). The tag-driven
> pipeline at `.github/workflows/release.yml` handles steps 2–6 of the
> release process end-to-end. This document captures the **operator
> checklist** for cutting a release and the **manual fallback** for
> emergencies when the workflow is unavailable.
>
> Pre-requisites (one-time):
>
> - PyPI Trusted Publisher entry pointing at `.github/workflows/release.yml`
>   on the `johrenberger/software-engineering-harness` project.
> - A matching TestPyPI Trusted Publisher entry for `v*rc*` tags.
> - A GitHub environment named `pypi` with required reviewers (set at
>   Settings → Environments). The `publish-pypi` job will block on it.

## Release process overview

1. **Pre-release**: confirm `main` is green, no open P0 issues.
2. **Cut the release**: tag the commit with `v<MAJOR>.<MINOR>.<PATCH>`.
3. **CI auto-runs**: SBOM (CycloneDX) + provenance attestation are
   emitted on every push to `main`.
4. **Audit**: download the SBOM, verify the attestation, and publish
   a GitHub Release that includes both as binary attachments.
5. **PyPI publish** (when G18 lands): `pypa/gh-action-pypi-publish`
   uploads the wheel + sdist after Sigstore-signing them.

## Step-by-step

### 0. Pre-flight

```bash
git checkout main
git pull --ff-only
gh issue list --label "release-blocker"  # must be empty
```

### 1. Cut the release commit

Pick the version. For now (`0.1.x` alpha), use SemVer:

* Increment `MAJOR` for breaking schema/API changes.
* Increment `MINOR` for new features.
* Increment `PATCH` for fixes-only releases.

```bash
# Edit pyproject.toml: version = "0.2.0"
$EDITOR pyproject.toml

git add pyproject.toml
git commit -m "chore(release): prepare v0.2.0"
```

### 2. Tag + push

Tag the commit and push the tag (this triggers the `release.yml`
workflow once G18 lands — for now, just CI re-runs on main).

```bash
git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

### 3. CI artifacts (automatic on push to main / tag)

Once you push, two artifacts drop from CI:

| Artifact | Format | Producer | Verification |
|---|---|---|---|
| `sbom` (artifact) | `sbom-cyclonedx.json` | `anchore/sbom-action@v0` | `python -c "import json, jsonschema; json.load(open('sbom-cyclonedx.json'))"` |
| Build provenance | Sigstore attestation | `actions/attest-build-provenance@v1` | `gh attestation verify sbom-cyclonedx.json --repo <repo>` |

Download them:

```bash
gh run download --name sbom
gh attestation verify sbom-cyclonedx.json --repo johrenberger/software-engineering-harness
```

### 4. Audit the SBOM

The CycloneDX file lists every direct + transitive dep at the
released SHA. Sanity-check:

```bash
# Components count (expect 20-40 for this project)
jq '.components | length' sbom-cyclonedx.json

# Confirm no unknown licenses
jq '.components[].licenses[]?.license.id' sbom-cyclonedx.json | sort -u
```

Flag any dep that's a known supply-chain risk. For audit-grade SBOMs,
cross-reference with the [OpenSSF Security Scorecard](https://scorecard.dev/)
for the deps' upstream repos.

### 5. Verify the provenance attestation

The attestation is a Sigstore-signed claim that the produced SBOM came
from the `vX.Y.Z` tag on `main`. Consumers can verify:

```bash
gh attestation verify sbom-cyclonedx.json \
  --repo johrenberger/software-engineering-harness \
  --signer-workflow johrenberger/.github/workflows/ci.yml@<ref>
```

If verification fails, **do not publish** — investigate the workflow
log for tampered artifacts or supply-chain MITM.

### 6. PyPI publish (automated — `.github/workflows/release.yml`)

Pushing a `v*` tag fires `release.yml`, which:

1. Verifies that the tag version matches `pyproject.toml`, `__version__`,
   and `CHANGELOG.md` via `scripts/check_version_drift.py`.
2. Builds wheel + sdist with `python -m build` on Python 3.12 and 3.13.
3. Generates a CycloneDX SBOM with `anchore/sbom-action`.
4. Signs build provenance (SLSA L1) with `actions/attest-build-provenance`.
5. Sigstore-signs wheel + sdist with `sigstore-python` (keyless via OIDC).
6. Publishes to **TestPyPI** for `vX.Y.Z-rcN` tags, or to **PyPI** for
   `vX.Y.Z` tags via `pypa/gh-action-pypi-publish` (Trusted Publisher).
7. Creates a GitHub Release with auto-generated release notes, attaching
   wheel + sdist + Sigstore bundles + SBOM as binary assets.

**Manual fallback** — if the workflow is broken or unavailable:

```bash
python -m build --sdist --wheel
python -m twine check dist/*
python -m twine upload --repository testpypi dist/*  # or 'pypi' for prod
```

Manual fallback is **not** the normal path — investigate the workflow
log and re-cut the tag once the pipeline is healthy.

## Post-release

* Bump `version` in `pyproject.toml` to the next dev version:
  `0.2.0 → 0.3.0.dev0`.
* Open a GH Release pointing at the tag.
* Pin the release in Dependabot (`ignore` rules for the released SHA).
* Update CHANGELOG (when it exists — separate work item).

## Rollback

If a release has a critical issue:

1. Yank from PyPI (when published): `pip yank <dist>`.
2. Delete the GitHub Release (creates a U-shaped workflow gap;
   coordinate with consumers first).
3. Push a `vX.Y.Z+1` patch release that fixes the issue.
4. Re-emit provenance for the patch release.

Yanking is non-destructive — the artifacts remain download-able but
pip refuses to install by default.

## Future work

* Auto-generated CHANGELOG from conventional-commits.
* `pip-audit` post-publish gate (block install on known vulns).
* Multi-arch wheel builds (ci.yml matrix is x86_64 only today).

## See also

* [SECURITY.md](../SECURITY.md) — vulnerability reporting.
* [Supply-chain hardening docs](../dev/architecture.md) — when it exists.
