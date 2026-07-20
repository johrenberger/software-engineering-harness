# I2 — Architecture overview doc

**Status:** ✅ MERGED (`e5dc15f`)
**Branch:** `agent/i2-architecture-overview` → `main`
**PR:** [#45](https://github.com/johrenberger/software-engineering-harness/pull/45)
**Commit:** `e5dc15f` — `docs(arch): I2 system-level service graph + honesty matrix`

## What landed

I2 ships a **system-level service graph** that complements the existing
orchestrator-focused [`docs/architecture.md`](architecture.md). The
new doc is a high-level map for operators and contributors — it
doesn't duplicate the orchestrator internals but provides the "where
do I look when X happens?" routing.

### `docs/architecture-overview.md` — NEW (220 lines)

Sections:
1. **At a glance** — subsystems table (Controller, Orchestrator,
   Sandbox, CI, Observability, Artifacts, Telegram).
2. **High-level data flow** — ASCII diagram showing entry points →
   controller → orchestrator → sandbox / CI / observability →
   telegram.
3. **Subsystem contracts** — Protocol + production + fake pattern.
4. **Supporting packages table** — dashboard, pipeline, execution,
   repository, phases, review, delivery, validation, domain,
   models, telegram_runtime (so every package under `src/seharness/`
   is documented).
5. **Run lifecycle diagram** — 12-phase pipeline + state machine.
6. **Storage layout** — `.openclaw-runs/orchestrator/<run_id>/` with
   `repo_profile.json`, `specification.json`, `plan.json`,
   `execution/<task_id>/...`, `trace.jsonl`, etc.
7. **What is NOT yet wired honesty matrix** — Idempotency (E1),
   Optimistic concurrency (E2), SQLite-backed ledger (B),
   Cancellation propagation (E4), Approval gates (E7), Schema
   migration (E3), Real model adapters (F), PyPI publishing (G18),
   Branch protection (G19), Maintained (time-based).
8. **Composition rule** — no bypassing the orchestrator (layer-6
   auto-merge prevention).

### `tests/unit/docs/test_architecture_overview.py` — NEW (24 tests)

- **`test_doc_exists`** — file present.
- **`test_subsystem_package_exists`** (parametrized × 7) — every
  subsystem's directory actually exists.
- **`test_doc_lists_all_subsystem_packages`** — every package under
  `src/seharness/` (excluding `skills`/`__pycache__`) is mentioned
  in the doc.
- **`test_doc_describes_12_phase_pipeline`** — all 11 phase names
  appear.
- **`test_doc_describes_terminal_states`** — completed / failed /
  blocked / paused all present.
- **`test_doc_describes_storage_layout`** — paths + filenames pinned.
- **`test_doc_describes_protocols`** — Protocol pattern + mutation
  killer tests referenced.
- **`test_honesty_matrix_present`** — "NOT YET" appears.
- **`test_honesty_matrix_lists_idempotency`** — E1 mentioned.
- **`test_honesty_matrix_lists_concurrency`** — E2 mentioned.
- **`test_honesty_matrix_lists_real_model_adapters`** — Codex + MiniMax.
- **`test_honesty_matrix_references_clusters`** — E1/E2/E4/E7/G18/G19.
- **`test_doc_does_not_claim_pypi_published`** — anti-claim.
- **`test_doc_does_not_claim_branch_protection`** — anti-claim.
- **`test_doc_links_to_orchestrator_doc`** — cross-reference.
- **`test_doc_links_to_user_docs`** — user-facing docs linked.
- **`test_doc_links_to_evidence`** — evidence/ directory linked.
- **`test_doc_at_least_100_lines`** — substantive length.
- **`test_doc_has_status_callout`** — Alpha / v0.1.0 callout.

## Test counts

| Stage | Tests |
|---|---|
| Pre-I2 (post-G10) | 1637 |
| After I2 | **1661** (+24 I2 architecture honesty contracts) |

## Honesty matrix (canonical reference)

The I2 doc now serves as the **canonical reference** for "what we
actually do vs what we plan to do". Every cluster that wants to add
a NOT YET row needs to update this doc (and the test will catch
any drift).

## Cross-references

- `docs/architecture.md` (existing) — orchestrator internals.
- `docs/user/run.md` — running a feature end-to-end.
- `docs/user/sandbox.md` — sandbox profiles.
- `docs/user/traces.md` — trace records.
- `docs/engineering-dashboard.md` — G12 dashboard.
- `docs/evidence/` — PR-by-PR evidence files (this file lives there).

## Post-merge CI status

At merge time (00:39:52 UTC), all post-merge CI runs queued
(GitHub Actions backed up). Local test count: 1661 passed, 3
skipped. ruff clean. mypy strict clean.