"""Cluster M3-2: tests for ProviderEvidenceRecord + ProviderEvidenceWriter.

The corrective doc §"Evidence requirements" enumerates 22 fields
plus a "never persist" list (API keys, authorization headers,
raw credentials, hidden reasoning, unredacted secret-bearing
content). These tests pin both directions:

- The record schema accepts the 22 fields and rejects empty
  audit anchors.
- The writer appends records as JSONL.
- The redactor strips API-key / Bearer / Authorization / JWT /
  hex-token patterns.
- The writer's __post_init__ re-runs the redactor on the
  message it is given, so a caller cannot sneak a credential
  through a pre-formatted error message.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

# Pre-import the orchestrator's package init via the controller
# module to break the partial-init cycle. Without this, a
# fresh-process import of ``seharness.orchestrator.provider_evidence``
# would trigger the partial-init cycle documented in
# ``application_service.py``.
from seharness.controller.run_ledger import RunLedger  # noqa: F401
from seharness.orchestrator.provider_evidence import (
    ProviderEvidenceRecord,
    ProviderEvidenceWriter,
    redact_error_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(**overrides: object) -> ProviderEvidenceRecord:
    """Return a fully-populated ``ProviderEvidenceRecord``."""
    defaults: dict[str, object] = {
        "run_id": "run-001",
        "phase": "specification",
        "task_id": None,
        "provider": "minimax",
        "configured_model": "MiniMax-M3",
        "returned_model": "MiniMax-M3",
        "protocol": "openai-compatible",
        "endpoint_classification": "openai_compatible",
        "prompt_template_version": "specification@v1",
        "thinking_mode": True,
        "service_tier": "standard",
        "local_correlation_id": "corr-001",
        "provider_request_id": "req-abc",
        "input_tokens": 100,
        "output_tokens": 200,
        "duration_s": 0.42,
        "attempt_number": 1,
        "normalized_error_kind": None,
        "redacted_error_message": None,
    }
    defaults.update(overrides)
    return ProviderEvidenceRecord(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedactErrorMessage:
    """The redactor is the secret-handling layer of the
    evidence envelope. New patterns go here; one assertion
    per pattern so a regression points at the exact line.
    """

    def test_none_passes_through(self) -> None:
        assert redact_error_message(None) is None

    def test_empty_string_stays_empty(self) -> None:
        assert redact_error_message("") == ""

    def test_plain_message_unchanged(self) -> None:
        assert (
            redact_error_message("provider_failure: connection refused")
            == "provider_failure: connection refused"
        )

    def test_openai_style_key_redacted(self) -> None:
        msg = "got 401 from server with key sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        out = redact_error_message(msg)
        assert "sk-proj-" not in out
        assert "[REDACTED]" in out

    def test_minimax_style_sk_key_redacted(self) -> None:
        msg = "auth failed: sk-abcdef0123456789abcdef0123456789"
        out = redact_error_message(msg)
        assert "sk-abcdef" not in out

    def test_bearer_token_redacted(self) -> None:
        msg = "request failed: Bearer eyJabc123def456ghi789jkl"
        out = redact_error_message(msg)
        assert "Bearer" not in out or "[REDACTED]" in out
        # The original token value must be gone.
        assert "eyJabc" not in out

    def test_authorization_header_redacted(self) -> None:
        msg = "got error:\nAuthorization: Bearer secret123abc456def789\n\n"
        out = redact_error_message(msg)
        assert "secret123abc456def789" not in out
        assert "Authorization" not in out

    def test_minimax_api_key_assignment_redacted(self) -> None:
        msg = "env was: MINIMAX_API_KEY=sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        out = redact_error_message(msg)
        assert "sk-proj-" not in out
        assert "AbCdEfGhIjKlMnOpQrStUvWxYz" not in out

    def test_jwt_token_redacted(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.signature_part_here"
        msg = f"received token {jwt}"
        out = redact_error_message(msg)
        assert "eyJhbGciOiJIUzI1NiJ9" not in out

    def test_hex_token_redacted(self) -> None:
        msg = "trace id: deadbeefcafebabe1234567890abcdef"
        out = redact_error_message(msg)
        assert "deadbeefcafebabe" not in out

    def test_all_redacted_returns_empty_string(self) -> None:
        """When every visible character is secret-shaped, the
        message is replaced with an empty string (not ``None``)
        so the operator can see that *something* was redacted.
        """
        msg = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        out = redact_error_message(msg)
        assert out == ""

    def test_mixed_message_partial_redaction(self) -> None:
        msg = "key sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 invalid"
        out = redact_error_message(msg)
        assert "sk-proj-" not in out
        assert "invalid" in out


# ---------------------------------------------------------------------------
# ProviderEvidenceRecord
# ---------------------------------------------------------------------------


class TestProviderEvidenceRecordValidation:
    """The record schema rejects empty audit anchors and
    non-positive attempt numbers. Defaults are honored so
    the constructor signature stays permissive.
    """

    def test_minimal_construction_works(self) -> None:
        r = ProviderEvidenceRecord(
            run_id="r",
            phase="specification",
            provider="minimax",
            configured_model="MiniMax-M3",
            returned_model="MiniMax-M3",
            protocol="openai-compatible",
            endpoint_classification="openai_compatible",
            prompt_template_version="specification@v1",
            local_correlation_id="c",
            duration_s=0.1,
            attempt_number=1,
        )
        assert r.thinking_mode is None
        assert r.service_tier is None
        assert r.input_artifact_hashes == ()

    def test_empty_run_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            _make_record(run_id="")

    def test_empty_phase_rejected(self) -> None:
        with pytest.raises(ValueError, match="phase"):
            _make_record(phase="")

    def test_empty_configured_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="configured_model"):
            _make_record(configured_model="")

    def test_empty_local_correlation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="local_correlation_id"):
            _make_record(local_correlation_id="")

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match="duration_s"):
            _make_record(duration_s=-1.0)

    def test_zero_attempt_number_rejected(self) -> None:
        with pytest.raises(ValueError, match="attempt_number"):
            _make_record(attempt_number=0)

    def test_post_init_runs_redactor_on_error_message(self) -> None:
        """The writer's contract is 'no raw credential ever
        reaches disk'. ``__post_init__`` runs the redactor one
        more time on the input message so a caller that
        already-formatted the message can't sneak a credential
        past the writer.
        """
        r = _make_record(
            redacted_error_message=(
                "auth failed with key sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
            )
        )
        assert r.redacted_error_message is not None
        assert "sk-proj-" not in r.redacted_error_message


# ---------------------------------------------------------------------------
# ProviderEvidenceWriter
# ---------------------------------------------------------------------------


class TestProviderEvidenceWriter:
    """The writer appends JSONL records; reading them back
    round-trips through :class:`ProviderEvidenceRecord`.
    """

    def test_first_record_creates_evidence_dir_and_file(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        writer.record(call=_make_record())
        assert (tmp_path / "evidence" / "evidence.jsonl").exists()

    def test_subsequent_records_append(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        writer.record(call=_make_record(run_id="r1", phase="specification"))
        writer.record(call=_make_record(run_id="r2", phase="planning"))
        writer.record(call=_make_record(run_id="r3", phase="implementation"))
        assert writer.record_count == 3
        records = list(writer.records())
        assert len(records) == 3
        assert [r.run_id for r in records] == ["r1", "r2", "r3"]

    def test_records_round_trip_through_jsonl(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        original = _make_record(
            input_artifact_hashes=("h1", "h2", "h3"),
            output_artifact_hash="ohash",
        )
        writer.record(call=original)
        records = list(writer.records())
        assert len(records) == 1
        r = records[0]
        assert r.run_id == original.run_id
        assert r.configured_model == original.configured_model
        assert r.returned_model == original.returned_model
        assert r.input_artifact_hashes == ("h1", "h2", "h3")
        assert r.output_artifact_hash == "ohash"

    def test_records_iter_empty_when_no_file(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        assert list(writer.records()) == []

    def test_clock_injection(self, tmp_path: Path) -> None:
        fixed = datetime(2026, 7, 22, 15, 0, 0)
        writer = ProviderEvidenceWriter(
            evidence_dir=tmp_path / "evidence",
            clock=lambda: fixed,
        )
        writer.record(call=_make_record())
        records = list(writer.records())
        assert records[0].timestamp == fixed.isoformat()

    def test_close_does_not_break_reopen(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        writer.record(call=_make_record(run_id="r1"))
        writer.close()
        # Reopen by constructing a new writer over the same dir.
        writer2 = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        writer2.record(call=_make_record(run_id="r2"))
        records = list(writer2.records())
        assert len(records) == 2

    def test_no_credential_in_persisted_line(self, tmp_path: Path) -> None:
        """The persisted JSONL line must not contain any
        secret-shaped substring, even if the caller forgot
        to redact first.
        """
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        writer.record(
            call=_make_record(
                redacted_error_message=("auth failed: sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
            )
        )
        raw = (tmp_path / "evidence" / "evidence.jsonl").read_text()
        assert "sk-proj-" not in raw
        assert "Authorization" not in raw

    def test_record_count_reflects_appends(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(evidence_dir=tmp_path / "evidence")
        assert writer.record_count == 0
        writer.record(call=_make_record())
        assert writer.record_count == 1
        writer.record(call=_make_record())
        writer.record(call=_make_record())
        assert writer.record_count == 3

    def test_custom_filename(self, tmp_path: Path) -> None:
        writer = ProviderEvidenceWriter(
            evidence_dir=tmp_path / "evidence",
            filename="audit.jsonl",
        )
        writer.record(call=_make_record())
        assert (tmp_path / "evidence" / "audit.jsonl").exists()
        # Default filename is not created.
        assert not (tmp_path / "evidence" / "evidence.jsonl").exists()
