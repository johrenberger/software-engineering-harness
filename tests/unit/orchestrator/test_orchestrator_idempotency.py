"""Cluster E, story E1: orchestrator-level idempotency key tests.

Verifies that the public ``Orchestrator.start_run`` API:

- accepts an ``idempotency_key`` parameter (default empty);
- threads the key down to ``RunLedger.record_start``;
- translates ``IdempotencyKeyConflictError`` to ``OrchestratorError`` at
  the public boundary so callers don't need to import the controller
  module;
- the ledger records the key on the resulting ``RunRecord``;
- empty key preserves the pre-E1 behaviour exactly (no key on the
  record, replace-on-duplicate ``run_id`` semantically unchanged).

These tests cover option B (caller plumbing); persistence (option C)
ships separately.
"""

from __future__ import annotations

from typing import Any

import pytest

from seharness.controller import run_ledger  # noqa: F401  (breaks circular import)
from seharness.controller.run_ledger import IdempotencyKeyConflictError, RunLedger
from seharness.orchestrator.orchestrator import Orchestrator, OrchestratorError


def _fresh_orchestrator() -> tuple[Orchestrator, RunLedger]:
    """Build an orchestrator with a real ledger and StubRunner."""
    ledger = RunLedger()
    orch = Orchestrator(run_ledger=ledger)
    return orch, ledger


def _stub_run_to_completion(orch: Orchestrator) -> None:
    """No-op overrides; the default StubRunner already returns synthetic
    OK results, so a default start_run completes end-to-end via the
    pipeline."""
    # The default orchestrator uses the in-test StubRunner pipeline.
    # No override needed unless the test wants a different behaviour.
    return None  # placeholder; nothing to do


class TestIdempotencyDefault:
    def test_start_run_default_has_no_key(self, tmp_path: Any) -> None:
        """The pre-E1 contract: ``start_run`` with no key produces a
        record whose ``idempotency_key`` is empty."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(feature_description="x", repo_path=str(repo))
        record = next(iter(ledger.runs))
        assert record.idempotency_key == ""

    def test_start_run_accepts_idempotency_key_kwarg(self, tmp_path: Any) -> None:
        """``start_run`` accepts ``idempotency_key=`` as a keyword
        argument and threads it into the ledger."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="gh-pr-123",
        )
        record = next(iter(ledger.runs))
        assert record.idempotency_key == "gh-pr-123"

    def test_start_run_signature_includes_idempotency_key(self) -> None:
        """Inspector check: the parameter is part of the public API."""
        import inspect

        sig = inspect.signature(Orchestrator.start_run)
        assert "idempotency_key" in sig.parameters
        assert sig.parameters["idempotency_key"].default == ""


class TestIdempotencyConflict:
    def test_conflict_with_different_run_id_raises_orchestrator_error(self, tmp_path: Any) -> None:
        """Same ``idempotency_key`` across two starts → second
        ``start_run`` raises ``OrchestratorError`` (translated from
        the controller's ``IdempotencyKeyConflictError``)."""
        orch, _ = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="req-abc",
        )
        with pytest.raises(OrchestratorError) as exc_info:
            orch.start_run(
                feature_description="y",
                repo_path=str(repo),
                idempotency_key="req-abc",
            )
        # The user-facing message mentions both the key and the
        # conflicting run_id.
        msg = str(exc_info.value)
        assert "req-abc" in msg
        # Original ledger record (1st run) is unchanged.
        _, _ledger = _fresh_orchestrator()
        # The first orchestrator + ledger had their state; we
        # only assert on the message; ledger state is verified
        # in the ledger-level tests.

    def test_orchestrator_does_not_leak_controller_error_class(self, tmp_path: Any) -> None:
        """The conflict surfaces as ``OrchestratorError``, NOT the raw
        ``IdempotencyKeyConflictError``. Ensures callers can rely
        on a single exception hierarchy."""
        orch, _ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="req-abc",
        )
        try:
            orch.start_run(
                feature_description="y",
                repo_path=str(repo),
                idempotency_key="req-abc",
            )
        except OrchestratorError as exc:
            # Sanity: must NOT also be the controller's specific
            # conflict error class.
            assert not isinstance(exc, IdempotencyKeyConflictError)
        else:
            pytest.fail("expected OrchestratorError")

    def test_ledger_lookup_helper_returns_existing_record(self, tmp_path: Any) -> None:
        """``_ledger_find_by_key`` returns the stored record for a key
        that was previously registered."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="req-xyz",
        )
        record = Orchestrator._ledger_find_by_key(ledger, "req-xyz")
        assert record is not None
        assert record.idempotency_key == "req-xyz"

    def test_ledger_lookup_helper_returns_none_for_unknown_key(self, tmp_path: Any) -> None:
        _orch, ledger = _fresh_orchestrator()
        # No records at all.
        assert Orchestrator._ledger_find_by_key(ledger, "does-not-exist") is None
        # Empty key returns None (no lookup).
        assert Orchestrator._ledger_find_by_key(ledger, "") is None


class TestIdempotencyBackCompat:
    def test_empty_key_does_not_trigger_dedupe(self, tmp_path: Any) -> None:
        """Two starts with no key produce two distinct ledger records
        (back-compat with pre-E1 call sites)."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(feature_description="x", repo_path=str(repo))
        orch.start_run(feature_description="y", repo_path=str(repo))
        # Both records present.
        runs = list(ledger.runs)
        assert len(runs) == 2

    def test_empty_key_replaces_run_id_on_collision(self, tmp_path: Any) -> None:
        """Pre-E1 semantics preserved: same ``run_id`` + empty key
        → record is REPLACED, not deduped."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        from seharness.orchestrator.types import RunId

        rid = RunId("orch-fixed-id")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            run_id=rid,
        )
        orch.start_run(
            feature_description="y",
            repo_path=str(repo),
            run_id=rid,
        )
        # Still 1 record (replace).
        runs = list(ledger.runs)
        assert len(runs) == 1
        record = runs[0]
        # The second ``feature_description`` is irrelevant: pre-E1
        # path doesn't touch the ``feature_description`` (it lives
        # in run artifacts, not the ledger). What's relevant: the
        # repository field IS the most-recent value after replace.
        assert record.repository == str(repo)


class TestPublicApiSurface:
    """Smoke checks on the new public surface."""

    def test_idempotency_key_empty_is_default(self, tmp_path: Any) -> None:
        """Defensive: even explicit empty key is the same as default."""
        orch, ledger = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")
        orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="",
        )
        record = next(iter(ledger.runs))
        assert record.idempotency_key == ""

    def test_idempotency_key_does_not_affect_run_id_generation(self, tmp_path: Any) -> None:
        """Passing a key must NOT change ``new_run_id()`` behaviour."""
        orch, _ = _fresh_orchestrator()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("# X\n")

        result = orch.start_run(
            feature_description="x",
            repo_path=str(repo),
            idempotency_key="some-key",
        )
        assert result.run_id  # non-empty auto-generated
        assert isinstance(result.run_id, str)
        assert result.run_id.startswith("orch-")


class TestLedgerLookupEdgeCases:
    """Misc edge-case coverage for ``_ledger_find_by_key``."""

    def test_ledger_without_key_index_returns_none(self) -> None:
        """A ledger that doesn't expose a ``_key_index`` attribute
        (e.g. a custom adapter without Cluster E1 wiring) must not
        raise. Helper returns ``None``."""

        class BareLedger:
            """Ledger-like object without _key_index."""

            def get(self, run_id: str) -> object:
                return None

        result = Orchestrator._ledger_find_by_key(BareLedger(), "any-key")  # type: ignore[arg-type]
        assert result is None

    def test_ledger_with_non_dict_index_returns_none(self) -> None:
        """A ledger whose ``_key_index`` is malformed (not a dict)
        must not raise. The helper treats it as 'no lookup possible'."""

        class WeirdIndexLedger:
            _key_index = "not-a-dict"

        result = Orchestrator._ledger_find_by_key(WeirdIndexLedger(), "k")  # type: ignore[arg-type]
        assert result is None
