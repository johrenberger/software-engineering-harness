# MiniMax-M3 Capability Matrix

This document distinguishes two claims that the corrective
doc requires us to keep separate:

- **Component** — the per-phase service exists, has unit
  tests, and is wired into the production-local
  composition.
- **Integrated vertical** — the full spec → plan →
  test-patch → RED → production-patch → GREEN →
  independent-review pipeline actually executes against
  the M3 composition end-to-end on a fixture repository.

A row marked **Integrated vertical: NO** is not a defect.
It is a recorded gap that the corrective doc either
defers (with a stated reason) or that a future PR will
close.

## Per-phase capability

| Phase | Component | Unit-tested? | Integrated vertical proved? | Proving test |
|---|---|---|---|---|
| Discovery | `RepositoryDiscovery` (orchestrator handler) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| Specification | `ModelBackedSpecificationService` (M3 router) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| Planning | `ModelBackedPlanningService` (M3 router) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| Test patch | `ControlledPatchService` (M3 router) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| RED | `run_red_green_cycle(...)` (with `SupportsValidationCommand`) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| Production patch | `ControlledPatchService` (M3 router) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| GREEN | `run_red_green_cycle(...)` + full validation suite | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` |
| Remediation | `BoundedFailureContext` + one bounded patch | YES | YES (offline) | `tests/unit/orchestrator/test_red_green_remediation.py` |
| Independent review | `IndependentMiniMaxReviewService` (separate router) | YES | YES (offline) | `tests/e2e/test_m3_offline_vertical.py` + `tests/unit/orchestrator/test_independent_review.py` |
| Local completion | `LocalCompletionPolicy` skipping DRAFT_PR / CI | YES | YES (offline) | `tests/unit/orchestrator/test_local_completion_policy.py` |
| Live M3 transport | `HttpMiniMaxTransport` against `https://api.minimax.io/v1` | YES (contract tests) | YES (smoke) | `tests/e2e/test_minimax_live_smoke.py` |
| Live M3 readiness | `ProviderReadiness` (catalog + direct-call fallback) | YES | YES (smoke) | `tests/unit/models/test_readiness_validation.py` |
| Live M3 vertical against deployed se-harness | n/a (integration) | n/a | **NO** | Closed via M3-5 Option (a); see `plans/m3-5-scope-check.md` |

## Component vs. integrated — what each row means

- **Component unit-tested (YES)** means there is at least
  one unit test that constructs the service in isolation,
  exercises its public API, and asserts on its outputs
  and on its model-evidence persistence.
- **Integrated vertical proved (YES)** means the offline
  M3 vertical-acceptance test (or, for transport, the
  live smoke) actually invokes the component as part of
  the full pipeline and observes a passing result.
- **NO** rows are *recorded gaps*, not *defects*. The
  corrective doc authorizes a `skipped_by_local_m3_acceptance_policy`
  framing for remote PR/CI work; the live deployed
  vertical is the same class of gap.

## Why the live deployed vertical is NO

The corrective doc's "Live M3 vertical acceptance"
section requires:

- A credentialed execution environment with `MINIMAX_API_KEY`
  in env.
- A deployed production se-harness endpoint (or a
  uvicorn-spun fixture).
- Immutable redacted evidence per phase.

The harness has no `seharness.controller:app` deployment
pipeline; only the offline-fixture M3-4 path is
proven. M3-5's Option-(a) closure accepts the offline
fixture + live transport smoke as the available evidence
and explicitly records the live-deployed vertical as a
future step that requires a deploy pipeline to close.

## Cross-references

- [`docs/vertical-acceptance.md`](./vertical-acceptance.md)
  — current vertical-acceptance index (M3 framing).
- [`docs/vertical-acceptance-cluster-n.md`](./vertical-acceptance-cluster-n.md)
  — historical M2.7 transport evidence (cluster N, 2026-07-21).
- [`plans/minimax-m3-corrective-processing-instructions.md`](../plans/minimax-m3-corrective-processing-instructions.md)
  — corrective refinement contract.
- [`plans/m3-5-scope-check.md`](../plans/m3-5-scope-check.md)
  — M3-5 Option-(a) closure decision.
- [`plans/m3-6-scope-check.md`](../plans/m3-6-scope-check.md)
  — M3-6 scope and risk register.

## How to update this matrix

When a future PR adds a new component or proves a new
integration path, add a row. Rows must not be silently
moved from NO to YES — the change must cite the
proving test.