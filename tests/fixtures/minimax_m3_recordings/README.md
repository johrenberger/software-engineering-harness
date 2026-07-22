# Cluster M3-4: MiniMax-M3 synthetic recordings

This directory holds **redacted synthetic recordings** that drive the
M3-4 offline vertical acceptance test
(`tests/e2e/test_m3_offline_vertical.py`).

These recordings are **NOT** captured from a real MiniMax-M3 call.
They are hand-written JSON payloads that:

1. Have valid `MiniMaxRequest` / `MiniMaxTransportResponse` shape.
2. Carry `model: "MiniMax-M3"` and `protocol: "native"`.
3. Are clearly marked as `recording_kind: "synthetic_redacted_placeholder"`
   in the manifest.
4. Pass through redaction: no `sk-` prefixes, no `Authorization:`
   headers, no JWT-shaped tokens, no hex blobs ≥ 32 chars.

The manifest's `swapped_by` field declares that **M3-5 (live
MiniMax-M3 vertical acceptance)** replaces these recordings with a
sibling set under `tests/fixtures/minimax_m3_recordings_live/` with
`recording_kind: "live_recording"`. The offline loader
(`tests/e2e/_bootstrap.py::active_recordings_dir`) prefers the live
manifest when present, so the same test code drives M3-4 (offline)
and M3-5 (live) without conditional branching.

## Layout

```
minimax_m3_recordings/
├── manifest.json                              # required, schema-versioned
├── spec_{request,response}.json               # phase 1: specification
├── plan_{request,response}.json               # phase 2: planning
├── implementation_test_patch_{request,response}.json         # phase 3a: test patch
├── implementation_production_patch_{request,response}.json  # phase 3b: production patch
├── review_{request,response}.json             # phase 4: independent review
└── README.md
```

## Why 2 implementation phases?

The corrective doc §"Canonical orchestrator integration" enumerates
**two** implementation sub-phases:

- **Test patch**: model emits a test-only diff (or no diff if tests
  already exist).
- **Production patch**: model emits the production diff after seeing
  the RED evidence.

The synthetic recordings honour this split so the offline test
exercises both code paths. In M3-5, the live recordings carry the
real split the model produces.

## Drift detection

The manifest declares a `sha256` of every response file. The
manifest validator (`tests/unit/models/test_m3_recording_manifest.py`)
re-computes the hash on load and fails if any drift, so a fixture
cannot silently change between commits.

The initial commit ships `sha256: "pending"`. A tooling step
(forthcoming) flips `pending` → `<hex>`; until that step lands,
the drift check is skipped (the fixture itself is committed to git
so any drift is visible in the diff).
