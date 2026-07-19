# Run traces (Cluster E, stories E5+E6)

Every orchestrator run emits a structured, append-only trace of
events to ``<execution_root>/<run_id>/trace.jsonl``. The trace is the
operator's window into what the harness actually did during a run
and the first place to look during incident response.

## What the trace captures

The trace is a JSONL file — one JSON object per line, sorted by
write order. Each event has at minimum:

```json
{
  "kind": "phase_started",
  "run_id": "orch-1be6afdb",
  "phase": "planning",
  "timestamp": 1784480935.7,
  "attempt": 0
}
```

The ``kind`` field is the discriminator. The harness currently emits
four event kinds:

| ``kind``              | When                                                          |
| --------------------- | ------------------------------------------------------------- |
| ``phase_started``     | A phase begins.                                               |
| ``phase_completed``   | A phase returned ``ok`` or ``skipped``.                       |
| ``phase_failed``      | A phase returned ``failed`` / ``blocked`` / ``paused``, or raised. |
| ``artifact_produced`` | A new file appeared under ``<run_dir>`` during a phase.       |

The ``error`` field on ``phase_failed`` carries the phase's detail
string (e.g. ``"exit code 1"``, ``"review verdict: reject"``).
The ``artifact_produced`` event carries a stable ``artifact_kind``
(``"spec"`` / ``"plan"`` / ``"profile"`` / ``"review"`` /
``"task_result"`` / ``"diff"`` / ``"file"`` / ``"directory"``) so
dashboards can group artefacts without re-parsing filenames.

## Where the trace lives

By default the trace is created at:

```
<execution_root>/<run_id>/trace.jsonl
```

where ``<execution_root>`` is ``OrchestratorConfig.execution_root``
(default: ``.openclaw-runs/orchestrator``) and ``<run_id>`` is the
run identifier (e.g. ``orch-1be6afdb``).

A typical run therefore produces:

```
.openclaw-runs/orchestrator/orch-1be6afdb/
├── trace.jsonl
├── repo-profile.json
├── specification.json
├── plan.json
├── review-verdict.json
└── execution/
    └── <task_id>/
        ├── red/
        └── green/
```

## Secret redaction

Every string value in every event is scrubbed via
:class:`seharness.observability.redactor.SecretRedactor` before
write. The redactor covers:

- Telegram bot tokens (``1234567890:AABBCC...``)
- OpenAI keys (``sk-...``, ``sk-proj-...``)
- Anthropic keys (``sk-ant-...``)
- GitHub personal access tokens (``ghp_...``, ``ghs_...``)
- AWS access key IDs (``AKIA...``)
- Generic ``password=...`` / ``token=...`` / ``api_key=...``
  assignments
- ``Authorization: Basic <base64>`` headers

Scrubbed values are replaced with the stable sentinel
``***REDACTED***``. Operators grep for this token in incident
response — its presence in a trace means a leak attempt was
defused.

False positives are acceptable: the redactor is conservative and
will over-match rather than under-match. A false positive is a
hidden secret that wasn't actually a secret; a false negative is a
real leak.

## Crash safety

The :class:`TraceWriter` calls ``os.fsync()`` after every line, so
events survive a hard process kill. Reopening the same path with
``append=True`` (the default) continues the existing log so a
``/resume`` from a crashed run picks up where the trace left off.

If ``fsync`` fails (e.g. on tmpfs in some containers), the writer
swallows the error and continues — durability is best-effort but
the event is at least in the kernel buffer.

## Disabling tracing

Tests and operators that don't want a trace file can disable it
with ``trace_writer=None``:

```python
from seharness.controller.run_ledger import RunLedger
from seharness.orchestrator import Orchestrator, OrchestratorConfig

ledger = RunLedger()
orch = Orchestrator(
    run_ledger=ledger,
    config=OrchestratorConfig(execution_root=".openclaw-runs/orchestrator"),
    trace_writer=None,  # disable trace.jsonl
)
```

No ``trace.jsonl`` is written. The orchestrator's ``PipelineEvent``
list (``result.events``) is unaffected.

## Injecting a custom writer

For tests that want to assert on emitted events directly:

```python
from pathlib import Path
from seharness.observability.trace import TraceWriter
from seharness.orchestrator import Orchestrator, OrchestratorConfig

custom = TraceWriter(path=Path("/tmp/mytrace.jsonl"))
orch = Orchestrator(
    run_ledger=ledger,
    config=OrchestratorConfig(execution_root="..."),
    trace_writer=custom,
)
# orch.start_run(...) emits into /tmp/mytrace.jsonl
custom.close()
```

The orchestrator does NOT close the injected writer — the caller
owns its lifecycle.

## Inspecting a trace

The trace is plain JSONL; any tool works. A few recipes:

```bash
# Pretty-print every event
jq . < .openclaw-runs/orchestrator/orch-*/trace.jsonl

# All failures
jq 'select(.kind == "phase_failed")' < .openclaw-runs/orchestrator/orch-*/trace.jsonl

# All artefacts produced by the specification phase
jq 'select(.kind == "artifact_produced" and .phase == "specification")' \
    < .openclaw-runs/orchestrator/orch-*/trace.jsonl

# Check that nothing leaked a secret (no ***REDACTED*** should appear)
grep -c 'REDACTED' .openclaw-runs/orchestrator/orch-*/trace.jsonl
```

A non-zero count for the last command means the redactor caught
something — review the trace to confirm the offending field is
truly scrubbed.

## Future event kinds

The trace model is intentionally extensible. Future slices will
add:

- ``model_request`` / ``model_response`` — captured provider call
  payloads (with redaction) for cost analysis and replay.
- ``tool_invocation`` — every tool call the model emits, with
  arguments and the tool's reply.
- ``state_transition`` — ``RunState`` transitions from
  ``pending`` → ``running`` → ``paused`` / ``blocked`` / ``complete``.
- ``human_approval`` — pending / granted / denied decisions on
  high-risk actions (E7).
- ``cost_record`` — per-model token usage and cost (E2 budget).

All new kinds use the same ``TraceEvent`` discriminated union and
inherit the same redaction pipeline.