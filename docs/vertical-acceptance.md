# Vertical Acceptance Evidence — MiniMax-M3

This document is the **current** vertical-acceptance index
for the se-harness MiniMax-M3 path. It supersedes the
cluster-N M2.7-era evidence preserved at
[`docs/vertical-acceptance-cluster-n.md`](./vertical-acceptance-cluster-n.md).

## Status

**Closed via M3-5 Option (a)**, recorded in
`plans/m3-5-scope-check.md` and the operator's
memory at `memory/2026-07-23.md`.

The M3 corrective refinement
(`plans/minimax-m3-corrective-processing-instructions.md`)
delivered the canonical orchestrator path:

```text
unmet requirement
 → repository discovery
 → MiniMax-M3 specification
 → MiniMax-M3 repository-aware plan
 → MiniMax-M3 test patch
 → genuine RED
 → MiniMax-M3 production patch
 → genuine GREEN
 → bounded MiniMax-M3 remediation if required
 → fresh-context MiniMax-M3 review of the final diff
 → evidence-backed local completion
```

The integration proof is:

1. The canonical production-local composition builds
   successfully and refuses silent fallback to M2.7
   (`build_minimax_m3_local_composition(...)`).
2. The orchestrator's phase handlers actually invoke that
   composition; no parallel demonstration pipeline
   remains.
3. The offline `/health` vertical-acceptance test runs the
   full pipeline end-to-end against a fixture repository
   with synthetic M3 recordings, producing a genuine
   RED → GREEN cycle and an independent M3 review.
4. The live transport smoke confirms the configured
   model is `MiniMax-M3` and the response carries the
   same model id (no silent substitution).
5. The capability matrix at
   [`docs/m3-capability-matrix.md`](./m3-capability-matrix.md)
   distinguishes "component unit-tested" from
   "integrated vertical proved" per phase.

The full `/health` vertical workflow against a **deployed
production se-harness** (not the offline fixture) is **not**
proven end-to-end. M3-5's Option-(a) closure explicitly
records this as an accepted gap; the corrective doc's
"Live M3 vertical acceptance" stage remains aspirational
until a production deploy pipeline exists.

## M3 cluster PR sequence

| PR | Cluster | Title | Commit |
|---|---|---|---|
| #83 | M3-1 | Cluster M3-1: model + protocol + review cross-field | `fe14120` |
| #84 | M3-2 | Cluster M3-2: build_minimax_m3_local_composition() | `bd93283` |
| #85 | M3-3 | Cluster M3-3: canonical orchestrator wiring | `01bcd71` |
| #86 | M3-4 | Cluster M3-4: offline MiniMax-M3 vertical acceptance | `41bd49b` |
| —  | M3-5 | Live M3 vertical acceptance — closed via Option (a) | (no merge commit) |
| #87 | M3-6 | Cluster M3-6: documentation and promotion | (this PR) |

M3-5 was closed by accepting M3-4's offline evidence as the
vertical-acceptance gate. No source code change ships for
M3-5; the closure is documented in
`plans/m3-5-scope-check.md`.

## 9-stage acceptance walkthrough

The MiniMax-M3 path inherits cluster N's 9-stage shape
because M3 is a model/protocol refinement of the same
orchestrator, not a redesign.

| # | Stage | Pinned by |
|---|---|---|
| 1 | **Discovery**: feature request parsed; repo profile discovered; spec rendered | `controlled_patches.py`/`SpecificationSchema.discovered_repo_profile_name` |
| 2 | **Planning**: spec → plan with `allowed_paths` per task; order enforced | `controlled_patches.py`/`PlanSchema.tasks[*].allowed_paths` |
| 3 | **Controlled patches**: plan → unified diff with purity/policy gates | `controlled_patches.py` — `PatchValidator.validate_purity`, `PatchPolicyChecker.check_paths_within_policy` |
| 4 | **Implementation**: patch applied in sandbox (`SandboxPatchApplier` with injectable `SupportsGitApply`) | `controlled_patches.py` — `SandboxPatchApplier` + `SandboxPatchApplier.run(...)` |
| 5 | **Validation**: real command evidence (pytest/mypy/ruff) via injectable runner | `red_green_cycle.py` — `run_red_green_cycle` + `SupportsValidationCommand` Protocol |
| 6 | **Red→Green remediation**: bounded failure context → model adapter → one patch | `red_green_cycle.py` — `BoundedFailureContext`, `RedGreenCycleResult`; `minimax_budget_tracker.py` — `MiniMaxBudgetTracker` |
| 7 | **Independent review**: separate router; no implementation history in prompt; rejection blocks delivery; malformed output never approves | `independent_review.py` — `IndependentMiniMaxReviewService`, `assert_review_blocks_completion`, `ForbiddenTokenReviewPromptVerifier` |
| 8 | **Delivery**: model-neutral delivery packaging; idempotent; gated on review `approval` | WP6 — `ModelBackedDeliveryService` (reused; not modified by cluster M3) |
| 9 | **Audit**: red-green-cycle evidence persisted to `<run_dir>/red-green-cycle.json`; M3 vertical-acceptance artifacts at `evidence/m3-*/` | `red_green_cycle.py` — `persist_cycle_result` |

## MiniMax-M3 production model

The configured production model is exactly
`MiniMax-M3`. There is **no silent fallback** to M2.7 or
any other model:

- `MINIMAX_MODEL` defaults to `MiniMax-M3` when unset
  (PR #83 / `fe14120`).
- `build_minimax_m3_local_composition(...)` rejects any
  configured model other than `MiniMax-M3` (PR #84 /
  `bd93283`).
- The orchestrator's per-phase evidence records the
  configured and returned model id; CI / offline-vertical
  tests assert equality.
- Readiness is capability-based (catalog verification or
  direct-call verification); static `LIVE` declarations
  are not accepted as proof.

## MiniMax-M2.7 compatibility

M2.7 remains available as a **transport-compatibility**
option (catalog availability and request/response shape
have been verified). It is **not** an accepted production
target for the corrective refinement. Evidence:

- The M2.7 catalog and chat-completions evidence from
  cluster N (2026-07-21, commit `fc8d38d`) is preserved
  at
  [`docs/vertical-acceptance-cluster-n.md`](./vertical-acceptance-cluster-n.md).
  Read it as **historical transport evidence**, not as
  current-model acceptance.
- The live smoke test
  (`tests/e2e/test_minimax_live_smoke.py`) accepts
  `MINIMAX_MODEL=MiniMax-M2.7` only when explicitly set
  by a credentialed operator for backwards-compatibility
  verification. Production paths assert
  `MINIMAX_MODEL == "MiniMax-M3"`.

## Reproduction recipe (live M3 smoke)

```bash
# 1. Configure env
export MINIMAX_API_KEY="***"  # credentialed operator value
export MINIMAX_MODEL="MiniMax-M3"
export RUN_MINIMAX_LIVE_TEST=1

# 2. Run the live smoke test
pytest tests/e2e/test_minimax_live_smoke.py -v

# 3. Verify artifact
cat tests/e2e/_artifacts/minimax_live_smoke.json
# expected: configured_model_id == "MiniMax-M3",
#           model_id_returned == "MiniMax-M3",
#           error_kind is null,
#           error_message is null
```

### Live smoke artifact shape

The smoke test writes a JSON artifact with the shape:

| Field | Meaning |
|---|---|
| `endpoint` | Tested URL — either `https://api.minimax.io/v1/chat/completions` (chat) or `https://api.minimax.io/v1/models` (catalog) |
| `configured_model_id` | What the harness asked for (must be `MiniMax-M3` for production) |
| `model_id_returned` | What the provider returned in `model` (must equal `configured_model_id`) |
| `duration_s` | Wall-clock latency of the call |
| `error_kind` | `null` on success; one of `auth`, `provider_failure`, `timeout`, `malformed_output` on failure |
| `error_message` | Redacted; `null` on success |
| `request_id` | Provider correlation id when available |
| `redacted_content` | Assistant message with credentials/secrets stripped |
| `commit_sha` | The commit that produced this artifact |
| `timestamp_utc` | ISO-8601 UTC timestamp |

The artifact is git-ignored at
`tests/e2e/_artifacts/minimax_live_smoke.json` so credentials
do not leak.

## Reproduction recipe (offline M3 vertical)

```bash
# 1. No env vars needed; recordings are checked in.
pytest tests/e2e/test_m3_offline_vertical.py -v

# Expected: single test class, single test method,
# 17+ hard assertions, no skips, no conditional logic.
```

The offline vertical-acceptance test exercises the full
spec → plan → test-patch → RED → production-patch → GREEN
→ independent-review pipeline against the fixture repo at
`tests/fixtures/health_fixture_repo/` with synthetic
recordings derived from real M3 calls (manifest at
`tests/fixtures/minimax_m3_recordings/manifest.json`).

## Capability matrix

See [`docs/m3-capability-matrix.md`](./m3-capability-matrix.md)
for the per-phase breakdown of "component unit-tested"
vs. "integrated vertical proved."

## What this PR does NOT verify

- It does **not** verify the full SPEC / PLAN / IMPL /
  RED-GREEN / REVIEW / DELIVERY cycle end-to-end against
  the live MiniMax-M3 endpoint with a real fixture repo.
  That requires a production deploy pipeline
  (none exists) and a credentialed operator. The
  corrective doc records this gap as an aspirational
  future step.
- It does **not** validate that production deployments
  of the M3 composition would succeed against an
  arbitrary account. The contract pin from M3-1 (empty
  model_id or absent key → production refuses to start)
  is enforced; a credentialed operator must run their
  own credentialed acceptance test on their own account.
- It does **not** remove the stop gate on other deferred
  work. Per the corrective doc, deferred work remains
  paused until a fresh audit approves the refinement.

## Stop-gate reminder

Per the corrective doc:

> Do not resume deferred work before M3-6 is complete
> and a fresh audit approves the refinement.
> A commit or document labeled `DRAFT` must not be merged
> as a substitute for passing the gate.

This document replaces the prior DRAFT-framed
`vertical-acceptance.md` with a current-acceptance index.
No other deferred work is opened by this PR.