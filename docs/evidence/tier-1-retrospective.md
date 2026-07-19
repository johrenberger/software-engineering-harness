# Tier 1 retrospective — 2026-07-19

**Tier 1 scope:** four S-sized slices, ~half-day each. Lift coverage,
harden supply chain, ship the matrix, fix the README honesty gap.

**Tier 1 result:** ✅ all 4 PRs merged, post-merge CI green on both legs.

## Slice outcomes

| PR | Title | Branch | Merged at | CI run |
|---|---|---|---|---|
| #32 | G3 Python matrix 3.12 + 3.13 | `agent/g3-python-matrix` | `1822053` | 29704505289 |
| #33 | G4 Actions SHA pinning | `agent/g4-actions-sha-pin` | `44bb1ce` | 29704806747 |
| #34 | G1 lift coverage 88 → 89 | `agent/g1-lift-coverage` | `c4cda1f` | 29705495637 |
| #35 | I1+I4 README honesty rewrite | `agent/i1-i4-readme-honesty` | `ba57fb0` | 29705748202 |

## Cumulative test growth

| Stage | Tests |
|---|---|
| Pre-Tier-1 (post PR #31) | 1515 |
| After PR #32 (G3) | 1522 |
| After PR #33 (G4) | 1528 |
| After PR #34 (G1 lift) | 1552 |
| After PR #35 (I1+I4) | **1589** |
| **Net Tier 1** | **+74 tests** |

## Coverage progression

| Stage | Combined coverage |
|---|---|
| Pre-Tier-1 | 88.22% |
| After PR #34 | **89.17%** (+0.95 pp) |
| **fail_under floor** | **88 → 89** |

## Supply chain

PR #33 SHA-pinned 9 unique actions across `ci.yml` + `dashboard.yml`,
eliminating tag-hijack risk on the workflow runners:

- actions/checkout@v4 → `34e114876b0b11c390a56381ad16ebd13914f8d5`
- actions/setup-python@v5 → `a26af69be951a213d495a4c3e4e4022e16d87065`
- actions/upload-artifact@v4 → `ea165f8d65b6e75b540449e92b4886f43607fa02`
- actions/download-artifact@v4 → `d3f86a106a0bac45b974a628896c90dbdf5c8093`
- actions/configure-pages@v5 → `983d7736d9b0ae728b81ab479565c72886d7745b`
- actions/upload-pages-artifact@v3 → `56afc609e74202658d3ffba0e8f6dda462b719fa`
- actions/deploy-pages@v4 → `d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e`
- actions/attest-build-provenance@v1 → `ef244123eb79f2f7a7e75d99086184180e6d0018` (annotated → dereferenced)
- anchore/sbom-action@v0 → `e22c389904149dbc22b58101806040fa8d37a610`

PR #32 added the 3.12 + 3.13 matrix with `fail-fast: false`, ensuring
both legs run even if one breaks.

## Honesty

PR #35 added 15 contract tests pinning README honesty. The README's
Status section is now machine-checked:

- must distinguish ✅ works / ⚠️ partial / ❌ not doing
- must explicitly say PyPI is not yet published
- must mention v0.1.0 / Alpha
- cannot claim public dashboard bind works
- must link to docs/user/{traces,sandbox}.md when mentioning them
- must link to SECURITY.md

## Post-merge verification

The merged main runs both 3.12 + 3.13 legs green (CI run 29705814369):

- coverage 89.17% (gate 89% reached)
- 1589 tests pass
- dashboard re-publishes successfully (run 29705814376) to
  <https://johrenberger.github.io/software-engineering-harness/>

## Lessons

1. **Slice-by-slice keeps CI failures isolated.** PR #29's G7 failure
   became fixable as PR #30 → #31 without rolling back the supply-chain
   work. Same model applies here: each Tier 1 PR is independently
   mergeable.

2. **The PR #34 coverage lift was *just barely* enough.** 89.17% sits
   0.17 pp above the 89 gate. One more lift to 90% needs ~3 more
   lines of test coverage. The SPEC value (90%) is reachable but
   requires targeting `orchestrator/orchestrator.py` (83%) or
   `validation/weakening.py` (78%).

3. **`fail_under` uses combined statements+branches**, not statements
   alone. Setting `fail_under = 90` while sitting at 89.17% combined
   is fine; setting it to 89 while at 88.99 would block the lift PR.
   Pin the floor to a *defensible* value, not the *desired* value.

4. **README honesty needs machine checks.** Without the 15 contract
   tests, the next contributor could delete the Status section "to
   simplify" and CI would not catch the regression.

5. **Network tests need markers.** The live dashboard URL test is
   `@pytest.mark.network` + skip-if-offline. Without that, every CI
   run in a sandboxed environment would fail.

## Next tier

**Tier 2** (3 medium slices, ~1 day each):

- **G5**: pip-audit + CodeQL + OpenSSF Scorecard
- **G10**: reduce checked-in construction artifacts (uv.lock to a
  smaller subset, image size claims, etc.)
- **I2**: architecture doc

Then **Tier 3** (5 medium slices, multi-PR):

- **G9**: `pip install seharness` release automation (uses G7 SBOM +
  provenance that landed in PR #31).
- **I3**: operational docs
- **E7**: human-approval gates
- **E4**: cancellation
- **F1**: provider/credential config docs
