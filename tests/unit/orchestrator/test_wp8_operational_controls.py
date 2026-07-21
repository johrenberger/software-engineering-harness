"""WP8 (Cluster H, story M) — Operational controls (budgets + leases + telemetry).

Acceptance criteria from the MiniMax handoff doc:

* Budgets for model usage, cost, tool calls, elapsed time, retries,
  files changed, and diff size.
* Budget exhaustion pauses or blocks with a clear reason.
* Two workers cannot advance the same run revision simultaneously.
* A dead worker's run becomes recoverable after lease expiry.
* OpenTelemetry-compatible trace export.

These tests pin the WP8 invariants at the integration seam:

1. ``RunBudgets`` enforces per-axis ceilings (``BudgetTracker.check``).
2. ``BudgetExhausted`` is raised by ``enforce()`` so the
   orchestrator can route to ``paused`` / ``blocked``.
3. Production profile refuses a fully-unlimited ``RunBudgets``.
4. ``LeaseStore.acquire`` refuses concurrent owners.
5. ``LeaseStore.renew`` is idempotent for the same owner.
6. ``LeaseStore.recover_expired`` returns the lapsed run_ids.
7. ``Tracer`` emits OTLP-shaped JSON spans with trace/span ids.
8. ``NullTracer`` is a drop-in no-op.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from seharness.config import RuntimeProfile

# Imports ordered to avoid a pre-existing circular-import trap: loading
# ``seharness.orchestrator.types`` directly as the FIRST orchestrator-
# related import triggers ``seharness.controller.application_service``'s
# ``from ..orchestrator import Orchestrator`` while ``seharness.orchestrator``
# is still mid-initialising, which fails. Loading a controller module
# first (which is itself triggered by the existing test suite via
# pytest's collection order) avoids the cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401  -- ordering fix
from seharness.orchestrator.budgets import (
    BudgetAxis,
    BudgetExhausted,
    BudgetOutcome,
    BudgetTracker,
    RunBudgets,
)
from seharness.orchestrator.leases import (
    Lease,
    LeaseConflict,
    LeaseNotFound,
    LeaseStore,
    default_lease_ttl_seconds,
    new_owner_token,
)
from seharness.orchestrator.telemetry import (
    NullTracer,
    Span,
    Tracer,
)
from seharness.orchestrator.types import OrchestratorConfig

# ---------------------------------------------------------------------------
# WP8.1 — RunBudgets and BudgetTracker
# ---------------------------------------------------------------------------


class TestBudgetsTracker:
    """WP8 acceptance: budget exhaustion pauses / blocks with a
    clear reason."""

    def test_unlimited_budgets_never_exhaust(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets())
        tracker.record(BudgetAxis.MODEL_TOKENS, 999_999_999)
        decision = tracker.check()
        assert decision.outcome is BudgetOutcome.OK

    def test_model_tokens_ceiling_raises(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(model_tokens=100))
        tracker.record(BudgetAxis.MODEL_TOKENS, 50)
        assert tracker.check().outcome is BudgetOutcome.OK
        tracker.record(BudgetAxis.MODEL_TOKENS, 60)
        decision = tracker.check()
        assert decision.outcome is BudgetOutcome.BLOCKED
        assert decision.exceeded_axis is BudgetAxis.MODEL_TOKENS
        assert decision.consumed == 110
        assert decision.ceiling == 100
        assert "model_tokens" in decision.reason

    def test_cost_ceiling_fractional(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(model_cost_usd=0.05))
        tracker.record(BudgetAxis.MODEL_COST_USD, 0.04)
        assert tracker.check().outcome is BudgetOutcome.OK
        tracker.record(BudgetAxis.MODEL_COST_USD, 0.02)
        decision = tracker.check()
        assert decision.outcome is BudgetOutcome.BLOCKED
        assert decision.exceeded_axis is BudgetAxis.MODEL_COST_USD

    def test_diff_size_bytes_ceiling(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(diff_size_bytes=1024))
        tracker.record(BudgetAxis.DIFF_SIZE_BYTES, 2048)
        decision = tracker.check()
        assert decision.outcome is BudgetOutcome.BLOCKED
        assert decision.exceeded_axis is BudgetAxis.DIFF_SIZE_BYTES

    def test_enforce_raises_budget_exhausted(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(tool_calls=1))
        tracker.record(BudgetAxis.TOOL_CALLS, 2)
        with pytest.raises(BudgetExhausted) as exc_info:
            tracker.enforce()
        assert exc_info.value.decision.exceeded_axis is BudgetAxis.TOOL_CALLS

    def test_consumption_isolated_per_axis(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(model_tokens=100, tool_calls=5))
        tracker.record(BudgetAxis.MODEL_TOKENS, 50)
        tracker.record(BudgetAxis.TOOL_CALLS, 2)
        # Consuming tool_calls to the ceiling does not affect model_tokens.
        tracker.set(BudgetAxis.TOOL_CALLS, 5)
        assert tracker.check().outcome is BudgetOutcome.BLOCKED
        assert tracker.check().exceeded_axis is BudgetAxis.TOOL_CALLS

    def test_axes_enum_is_stable(self) -> None:
        # Stable enum ordering matters because ``check()`` returns
        # the first exceeded axis in declaration order.
        assert [a.value for a in BudgetAxis] == [
            "model_tokens",
            "model_cost_usd",
            "tool_calls",
            "elapsed_seconds",
            "retries",
            "files_changed",
            "diff_size_bytes",
        ]

    def test_negative_record_rejected(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets())
        with pytest.raises(ValueError, match=">= 0"):
            tracker.record(BudgetAxis.MODEL_TOKENS, -1)

    def test_negative_set_rejected(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets())
        with pytest.raises(ValueError, match=">= 0"):
            tracker.set(BudgetAxis.MODEL_TOKENS, -1)

    def test_enforce_returns_ok_decision(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets(model_tokens=100))
        tracker.record(BudgetAxis.MODEL_TOKENS, 50)
        decision = tracker.enforce()
        assert decision.outcome is BudgetOutcome.OK

    def test_consumption_returns_copy(self) -> None:
        tracker = BudgetTracker(budgets=RunBudgets())
        tracker.record(BudgetAxis.MODEL_TOKENS, 50)
        snapshot = tracker.consumption()
        # Mutating the snapshot does not mutate the tracker.
        snapshot[BudgetAxis.MODEL_TOKENS] = 9999
        assert tracker.consumption()[BudgetAxis.MODEL_TOKENS] == 50.0

    def test_run_budgets_axes_helper(self) -> None:
        budgets = RunBudgets(model_tokens=100)
        assert BudgetAxis.MODEL_TOKENS in budgets.axes()


# ---------------------------------------------------------------------------
# WP8.2 — Production profile requires explicit budgets
# ---------------------------------------------------------------------------


class TestProductionBudgetsRequired:
    def test_production_rejects_unlimited_budgets(self) -> None:
        with pytest.raises(ValueError, match="explicit RunBudgets"):
            OrchestratorConfig(runtime_profile=RuntimeProfile.PRODUCTION)

    def test_production_accepts_any_explicit_axis(self) -> None:
        cfg = OrchestratorConfig(
            runtime_profile=RuntimeProfile.PRODUCTION,
            budgets=RunBudgets(retries=3),
        )
        assert cfg.runtime_profile is RuntimeProfile.PRODUCTION

    def test_development_allows_unlimited_budgets(self) -> None:
        # No exception — development is permissive.
        cfg = OrchestratorConfig(runtime_profile=RuntimeProfile.DEVELOPMENT)
        assert cfg.budgets.is_unlimited()

    def test_test_profile_allows_unlimited_budgets(self) -> None:
        cfg = OrchestratorConfig(runtime_profile=RuntimeProfile.TEST)
        assert cfg.budgets.is_unlimited()

    def test_invalid_lease_ttl_rejected(self) -> None:
        with pytest.raises(ValueError, match="lease_ttl_seconds"):
            OrchestratorConfig(lease_ttl_seconds=0.0)
        with pytest.raises(ValueError, match="lease_ttl_seconds"):
            OrchestratorConfig(lease_ttl_seconds=-1.0)


# ---------------------------------------------------------------------------
# WP8.3 — Worker leases
# ---------------------------------------------------------------------------


class TestLeaseStore:
    """WP8 acceptance: ``Two workers cannot advance the same run
    revision simultaneously.``"""

    def test_acquire_succeeds_for_fresh_run(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        lease = store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
        )
        assert lease.run_id == "orch-x"
        assert lease.worker_id == "w-1"
        assert lease.revision == 1

    def test_concurrent_owner_raises_conflict(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        store.acquire(run_id="orch-x", worker_id="w-1", revision=1)
        with pytest.raises(LeaseConflict):
            store.acquire(run_id="orch-x", worker_id="w-2", revision=1)

    def test_same_owner_token_is_idempotent(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        token = new_owner_token()
        first = store.acquire(run_id="orch-x", worker_id="w-1", revision=1, owner_token=token)
        second = store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token=token,
        )
        assert first.owner_token == second.owner_token
        assert first.acquired_at == second.acquired_at

    def test_renew_extends_ttl(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases", default_ttl_seconds=10.0)
        token = new_owner_token()
        lease = store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token=token,
        )
        first_ttl = lease.ttl_seconds()
        renewed = store.renew(
            run_id="orch-x",
            owner_token=token,
            ttl_seconds=120.0,
        )
        assert renewed.ttl_seconds() > first_ttl
        assert renewed.ttl_seconds() > 100.0

    def test_release_clears_lease(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        token = new_owner_token()
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token=token,
        )
        store.release(run_id="orch-x", owner_token=token)
        assert store.get("orch-x") is None

    def test_release_by_other_worker_raises(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token="token-a",
        )
        with pytest.raises(LeaseConflict):
            store.release(run_id="orch-x", owner_token="token-b")

    def test_release_of_nonexistent_lease_is_noop(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        # Should not raise.
        store.release(run_id="orch-missing", owner_token="any-token")

    def test_renew_missing_lease_raises_not_found(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        with pytest.raises(LeaseNotFound):
            store.renew(run_id="orch-missing", owner_token="any")

    def test_renew_by_other_owner_raises_conflict(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token="token-a",
        )
        with pytest.raises(LeaseConflict):
            store.renew(run_id="orch-x", owner_token="token-b")

    def test_iter_leases_yields_all(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        store.acquire(run_id="orch-a", worker_id="w-1", revision=1)
        store.acquire(run_id="orch-b", worker_id="w-2", revision=1)
        leases = list(store.iter_leases())
        assert {lease.run_id for lease in leases} == {"orch-a", "orch-b"}

    def test_load_from_existing_file(self, tmp_path: Path) -> None:
        root = tmp_path / "leases"
        store = LeaseStore(root)
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=2,
            owner_token="persisted-token",
        )
        # New store instance reads from the same directory.
        new_store = LeaseStore(root)
        loaded = new_store.get("orch-x")
        assert loaded is not None
        assert loaded.revision == 2
        assert loaded.owner_token == "persisted-token"

    def test_default_lease_ttl_seconds(self) -> None:
        import os

        assert default_lease_ttl_seconds() == 60.0
        os.environ["SEHARNESS_LEASE_TTL_SECONDS"] = "120"
        try:
            assert default_lease_ttl_seconds() == 120.0
        finally:
            del os.environ["SEHARNESS_LEASE_TTL_SECONDS"]

    def test_default_lease_ttl_invalid_env(self) -> None:
        import os

        os.environ["SEHARNESS_LEASE_TTL_SECONDS"] = "not-a-number"
        try:
            with pytest.raises(ValueError, match="must be a float"):
                default_lease_ttl_seconds()
        finally:
            del os.environ["SEHARNESS_LEASE_TTL_SECONDS"]

    def test_default_lease_ttl_negative_env(self) -> None:
        import os

        os.environ["SEHARNESS_LEASE_TTL_SECONDS"] = "-1"
        try:
            with pytest.raises(ValueError, match="must be > 0"):
                default_lease_ttl_seconds()
        finally:
            del os.environ["SEHARNESS_LEASE_TTL_SECONDS"]


class TestLeaseExpiryAndRecovery:
    """WP8 acceptance: ``A dead worker's run becomes recoverable
    after lease expiry.``"""

    def test_recover_expired_returns_lapsed_run_ids(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases", default_ttl_seconds=0.1)
        store.acquire(run_id="orch-a", worker_id="w-1", revision=1)
        store.acquire(run_id="orch-b", worker_id="w-1", revision=1)
        time.sleep(0.2)
        expired = store.recover_expired()
        assert set(expired) == {"orch-a", "orch-b"}
        # The leases have been removed from the store.
        assert store.get("orch-a") is None
        assert store.get("orch-b") is None

    def test_acquire_after_expiry_succeeds_for_new_owner(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases", default_ttl_seconds=0.1)
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token="token-a",
        )
        time.sleep(0.2)
        new_lease = store.acquire(
            run_id="orch-x",
            worker_id="w-2",
            revision=2,
        )
        assert new_lease.worker_id == "w-2"
        assert new_lease.revision == 2

    def test_lease_is_expired_method(self) -> None:
        now = datetime.now(tz=UTC)
        lease = Lease(
            run_id="orch-x",
            worker_id="w-1",
            owner_token="token",
            revision=1,
            acquired_at=now - timedelta(seconds=10),
            expires_at=now - timedelta(seconds=1),
        )
        assert lease.is_expired(now) is True
        assert lease.ttl_seconds(now) < 0

    def test_lease_round_trip_json(self) -> None:
        now = datetime.now(tz=UTC)
        lease = Lease(
            run_id="orch-x",
            worker_id="w-1",
            owner_token="token",
            revision=2,
            acquired_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        payload = lease.to_jsonable()
        restored = Lease.from_jsonable(payload)
        assert restored == lease


# ---------------------------------------------------------------------------
# WP8.4 — Multi-worker concurrent lease acquisition
# ---------------------------------------------------------------------------


class TestConcurrentLeaseAcquisition:
    """WP8 acceptance: ``Two workers cannot advance the same run
    revision simultaneously.``"""

    def test_two_workers_race_for_same_run(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        results: list[str] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def attempt(worker_id: str) -> None:
            barrier.wait()
            try:
                store.acquire(run_id="orch-x", worker_id=worker_id, revision=1)
                results.append(worker_id)
            except LeaseConflict as exc:
                errors.append(exc)

        t1 = threading.Thread(target=attempt, args=("w-1",))
        t2 = threading.Thread(target=attempt, args=("w-2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one worker won.
        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], LeaseConflict)


# ---------------------------------------------------------------------------
# WP8.5 — OpenTelemetry-compatible trace export
# ---------------------------------------------------------------------------


class TestTracerEmitsOTLPSpans:
    """WP8 acceptance: ``Provide OpenTelemetry-compatible trace
    export.``"""

    def test_span_records_attributes(self) -> None:
        tracer = Tracer()
        with tracer.span("_phase_implementation", attributes={"run_id": "orch-x"}) as span:
            span.set_attribute("phase", "implementation")
            span.add_event("evidence_recorded", attributes={"kind": "red"})
        # Spans are written to stdout by default; capture them
        # via a callable sink instead to inspect the payload.
        sink: list[dict[str, object]] = []
        local_tracer = Tracer(sink=sink.append)
        with local_tracer.span("test") as span:
            span.set_attribute("phase", "x")
        assert len(sink) == 1
        payload = sink[0]
        assert payload["name"] == "test"
        assert payload["status"] == {"code": "OK"}
        assert payload["attributes"]["phase"] == "x"
        assert payload["attributes"]["service.name"] == "seharness-orchestrator"
        assert isinstance(payload["context"]["trace_id"], str)
        assert isinstance(payload["context"]["span_id"], str)

    def test_span_records_error_status(self) -> None:
        sink: list[dict[str, object]] = []
        tracer = Tracer(sink=sink.append)
        with pytest.raises(RuntimeError), tracer.span("explode"):
            raise RuntimeError("kaboom")
        assert len(sink) == 1
        payload = sink[0]
        assert payload["status"] == {"code": "ERROR"}
        assert payload["attributes"]["exception.type"] == "RuntimeError"
        assert "kaboom" in str(payload["attributes"]["exception.message"])

    def test_nested_spans_share_trace_id(self) -> None:
        sink: list[dict[str, object]] = []
        tracer = Tracer(sink=sink.append)
        with tracer.span("outer") as outer, tracer.span("inner", parent=outer):
            pass
        assert len(sink) == 2
        outer_payload = next(p for p in sink if p["name"] == "outer")
        inner_payload = next(p for p in sink if p["name"] == "inner")
        assert outer_payload["context"]["trace_id"] == inner_payload["context"]["trace_id"]
        assert inner_payload["parent_id"] == outer_payload["context"]["span_id"]

    def test_file_sink_appends_one_span_per_line(self, tmp_path: Path) -> None:
        path = tmp_path / "spans.jsonl"
        tracer = Tracer(sink=path)
        with tracer.span("a"):
            pass
        with tracer.span("b"):
            pass
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            payload = json.loads(line)
            assert "context" in payload
            assert "trace_id" in payload["context"]


class TestNullTracerIsDropInReplacement:
    def test_null_tracer_span_context_manager(self) -> None:
        tracer = NullTracer()
        with tracer.span("test") as span:
            span.set_attribute("phase", "x")
        assert isinstance(span, Span)

    def test_null_tracer_emits_no_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        tracer = NullTracer()
        with tracer.span("test"):
            pass
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_null_tracer_start_end_span_returns_none(self) -> None:
        tracer = NullTracer()
        span = tracer.start_span("test")
        assert isinstance(span, Span)
        # end_span is a no-op.
        assert tracer.end_span(span) is None
        # close() is also a no-op.
        assert tracer.close() is None


class TestTracerStreamSink:
    def test_stream_sink_writes_to_handle(self) -> None:
        import io

        buffer = io.StringIO()
        tracer = Tracer(sink=buffer)
        with tracer.span("streamed"):
            pass
        line = buffer.getvalue().strip()
        payload = json.loads(line)
        assert payload["name"] == "streamed"

    def test_tracer_close_closes_handle(self) -> None:
        import io

        buffer = io.StringIO()
        tracer = Tracer(sink=buffer)
        tracer.close()
        # Writing to a closed StringIO raises ValueError.
        with pytest.raises(ValueError):
            buffer.write("")


class TestBuildTracerFromEnv:
    def test_off_returns_null_tracer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.telemetry import build_tracer_from_env

        monkeypatch.setenv("SEHARNESS_TRACE_SINK", "off")
        assert isinstance(build_tracer_from_env(), NullTracer)

    def test_stdout_returns_real_tracer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.telemetry import build_tracer_from_env

        monkeypatch.setenv("SEHARNESS_TRACE_SINK", "stdout")
        tracer = build_tracer_from_env()
        assert isinstance(tracer, Tracer)
        assert not isinstance(tracer, NullTracer)

    def test_path_returns_real_tracer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from seharness.orchestrator.telemetry import build_tracer_from_env

        path = tmp_path / "spans.jsonl"
        monkeypatch.setenv("SEHARNESS_TRACE_SINK", str(path))
        tracer = build_tracer_from_env()
        assert isinstance(tracer, Tracer)

    def test_unset_returns_null_tracer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from seharness.orchestrator.telemetry import build_tracer_from_env

        monkeypatch.delenv("SEHARNESS_TRACE_SINK", raising=False)
        assert isinstance(build_tracer_from_env(), NullTracer)


# ---------------------------------------------------------------------------
# WP8.6 — LeaseStore + BudgetTracker together (sanity)
# ---------------------------------------------------------------------------


class TestBudgetAndLeaseTogether:
    def test_lease_token_round_trip_with_budget(self, tmp_path: Path) -> None:
        store = LeaseStore(tmp_path / "leases")
        token = new_owner_token()
        store.acquire(
            run_id="orch-x",
            worker_id="w-1",
            revision=1,
            owner_token=token,
        )
        tracker = BudgetTracker(budgets=RunBudgets(model_tokens=1000))
        tracker.record(BudgetAxis.MODEL_TOKENS, 250)
        # Budget check is independent of the lease, but both must
        # hold for the run to advance.
        assert tracker.check().outcome is BudgetOutcome.OK
        assert store.get("orch-x") is not None
        assert store.get("orch-x").owner_token == token
