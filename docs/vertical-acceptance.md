# Vertical Acceptance Evidence (Cluster N, Step 8)

> ⚠️ **This PR is DRAFT.** It documents the credentialed live run
> evidence per the workplan exit criterion but does NOT promote
> the cluster N MiniMax-backed path to "production-ready" in
> general availability terms. Promotion requires a credentialed
> operator's review of this evidence.

## Live run summary

A credentialed live run was executed against the official
OpenAI-compatible endpoint (`https://api.minimax.io/v1`) on
**2026-07-21T23:24:22Z** against commit SHA
`fc8d38d2947d64ec6e42d785dcc50b6bcf203f0f` (PR #75 merge).

| Step | Endpoint | Method | Result |
|---|---|---|---|
| 1 | `https://api.minimax.io/v1/models` | GET | ✅ Returns catalog with `MiniMax-M2.7` listed |
| 2 | `https://api.minimax.io/v1/chat/completions` | POST | ✅ Returns real completion with `model: "MiniMax-M2.7"` |

The captured artifact is git-ignored at
`tests/e2e/_artifacts/minimax_live_smoke.json`. The chat
test artifact from this run:

```json
{
  "commit_sha": "fc8d38d2947d64ec6e42d785dcc50b6bcf203f0f",
  "configured_model_id": "MiniMax-M2.7",
  "duration_s": 1.3853,
  "endpoint": "https://api.minimax.io/v1/chat/completions",
  "error_kind": null,
  "model_id_returned": "MiniMax-M2.7",
  "host": "9e4827db9df0",
  "request_id": null,
  "redacted_content": "<<think>The user just typed \"ping\". This<<\/think>\\n\\n",
  "timestamp_utc": "2026-07-21T23:24:22.569342+00:00"
}
```

The catalog test artifact from the same run:

```json
{
  "commit_sha": "fc8d38d2947d64ec6e42d785dcc50b6bcf203f0f",
  "configured_model_id": "MiniMax-M2.7",
  "duration_s": 0.1199,
  "endpoint": "https://api.minimax.io/v1/models",
  "error_kind": null,
  "model_id_returned": null,
  "host": "9e4827db9df0",
  "timestamp_utc": "2026-07-21T23:24:15.220276+00:00"
}
```

Both endpoints were reachable; the configured model id
matched the catalog AND the response's `model` field
(no silent substitution per contract pin).

## 9-stage acceptance walkthrough

The workplan prescribes 9 vertical-acceptance stages.
Each is pinned to a specific piece of the cluster N code
that has shipped through PRs #70-#76.

| # | Stage | Pinned by |
|---|---|---|
| 1 | **Discovery**: feature request parsed; repo profile discovered; spec rendered | PR #73 (spec-plan) — `SpecificationSchema.discovered_repo_profile_name` (required, min_length=1) |
| 2 | **Planning**: spec → plan with allowed_paths per task; order enforced | PR #73 — `PlanSchema.tasks[*].allowed_paths`, `validate_plan_against_policy(...)` |
| 3 | **Controlled patches**: plan → unified diff with purity/policy gates | PR #74 (`controlled_patches.py`) — `PatchValidator.validate_purity`, `PatchPolicyChecker.check_paths_within_policy` |
| 4 | **Implementation**: patch applied in sandbox (`SandboxPatchApplier` with injectable `SupportsGitApply`) | PR #74 — `SandboxPatchApplier` + `SandboxPatchApplier.run(...)` |
| 5 | **Validation**: real command evidence (pytest/mypy/ruff) via injectable runner | PR #75 (`red_green_cycle.py`) — `run_red_green_cycle` + `SupportsValidationCommand` Protocol |
| 6 | **Red→Green remediation**: bounded failure context → model adapter → one patch | PR #75 — `BoundedFailureContext`, `RedGreenCycleResult`; PR #75 (`minimax_budget_tracker.py`) — `MiniMaxBudgetTracker` |
| 7 | **Independent review**: separate router; no implementation history in prompt; rejection blocks delivery; malformed output never approves | PR #76 (`independent_review.py`) — `IndependentMiniMaxReviewService`, `assert_review_blocks_completion`, `ForbiddenTokenReviewPromptVerifier` |
| 8 | **Delivery**: model-neutral delivery packaging; idempotent; gated on review `approval` | WP6 (PR #64) — `ModelBackedDeliveryService` (reused; not modified by cluster N) |
| 9 | **Audit**: red-green-cycle evidence persisted to `<run_dir>/red-green-cycle.json`; live smoke artifact at `tests/e2e/_artifacts/minimax_live_smoke.json` (git-ignored) | PR #75 — `persist_cycle_result`; PR #71 — `_record_artifact` |

## What this PR does NOT verify

- It does **not** verify the full SPEC / PLAN / IMPL / RED-GREEN
  / REVIEW / DELIVERY cycle end-to-end against the live
  MiniMax endpoint with a real fixture repo. That requires a
  disposable CI environment (outside cluster N scope).
- It does **not** validate that production deployments of
  cluster N composition would succeed against an arbitrary
  account. The contract pin from PR #70 (empty model_id or
  absent key → production refuses to start) is enforced; a
  credentialed operator must run their own credentialed
  acceptance test on their own account.
- It does **not** remove the `DRAFT` status from this PR. The
  promotion to a real release tag is a separate decision.

## Reproduction recipe

```bash
# 1. Configure env
export MINIMAX_API_KEY="sk-cp-…"  # credentialed operator value
export MINIMAX_MODEL="MiniMax-M2.7"  # or another model on the account's catalog
export RUN_MINIMAX_LIVE_TEST=1

# 2. Run the live smoke test
pytest tests/e2e/test_minimax_live_smoke.py -v

# 3. Verify artifact
cat tests/e2e/_artifacts/minimax_live_smoke.json
# expected: error_kind=null, error_message=null, configured_model_id == model_id_returned
```

Expected output of step 2:
```
tests/e2e/test_minimax_live_smoke.py::TestMiniMaxLiveSmoke::test_chat_completions_endpoint_reachable PASSED
tests/e2e/test_minimax_live_smoke.py::TestMiniMaxLiveSmoke::test_models_endpoint_lists_configured_model PASSED
```

Expected output of step 3 (chat run):
```json
{
  "configured_model_id": "MiniMax-M2.7",
  "model_id_returned": "MiniMax-M2.7",
  "error_kind": null,
  ...
}
```

## Open question for the credentialed operator

Are there scenarios in your account that this evidence
*doesn't* cover which the workplan exit criterion expects
covered? E.g.:

- Are there billing/quota error responses that this env's
  healthy key doesn't exercise? (HTTP 429 with `Retry-After`)
- Does `GET /v1/models` for your account return `MiniMax-M2.5`,
  `MiniMax-M2.1`, `MiniMax-M2` etc., and if so do you want
  them wired into a public catalog helper?
- Should the chat smoke test stub a 4-message conversation to
  exercise multi-turn routing?

If yes to any of these, file follow-ups on this DRAFT before
promoting out of DRAFT status.
