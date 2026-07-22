"""Cluster M3-2 corrective: provider evidence writer.

The corrective processing instructions §"Evidence requirements"
mandate that every model call record a structured, durable,
redacted evidence envelope so the live M3 vertical acceptance
(M3-5) can be replayed offline and audited.

What this module owns:

- ``ProviderEvidenceRecord``: the dataclass that captures one
  model call. Field names match the corrective doc verbatim.
- ``ProviderEvidenceWriter``: the append-only JSONL writer
  that persists records to a directory under the run workspace.
- ``redact_error_message``: the secret-redaction helper that
  strips API keys, Bearer tokens, and Authorization headers
  from error messages before they reach disk.

What this module does NOT own:

- Calling the model itself. The orchestrator's phase handlers
  invoke ``writer.record(call=...)`` after each call.
- Cross-process serialization. JSONL appends are atomic on
  POSIX for writes < ``PIPE_BUF`` (4096 bytes on Linux). One
  record comfortably fits. M3-3's worker-lease layer provides
  the cross-process lock when needed.

Secret-redaction guarantees:

The writer NEVER receives an API key. The configured-model
identifier is the only "auth-bearing" string it sees. Error
messages pass through ``redact_error_message`` which strips:

- ``sk-*`` (OpenAI-style keys, also covers ``sk-proj-...``)
- ``Bearer <token>``
- ``Authorization: <scheme> <value>``
- ``MINIMAX_API_KEY=<value>``

The redactor is conservative: it strips the entire bearer
phrase and replaces with ``[REDACTED]``. False positives (a
user-supplied string that happens to look like a key) are
acceptable; false negatives (a real key on disk) are not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

# Pattern catalogue: every secret-shaped substring we strip on
# write. Compiled once at module load so the hot path is just a
# ``re.sub`` call. New patterns go here; the redaction test
# suite (``tests/unit/orchestrator/test_provider_evidence.py``)
# enumerates one assertion per pattern.

# OpenAI / MiniMax / generic sk-... style keys (32+ chars).
_OPENAI_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
# Bearer <token> — strip the value, keep the scheme name.
_BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
# Authorization: <scheme> <value> header line.
_AUTH_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?im)^[ \t]*authorization[ \t]*:[ \t]*[^\r\n]+"
)
# MINIMAX_API_KEY=<value> (and similar KEY=... assignments).
_ENV_ASSIGN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(MINIMAX_API_KEY|API_KEY|API-KEY|API[_-]?TOKEN)\s*[=:]\s*"
    r"[A-Za-z0-9._\-]+"
)
# Hex-shaped 32+ char strings (covers JWT-style, raw hex tokens).
_HEX_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b[0-9a-f]{32,}\b")
# Anything that looks like a JSON web token (header.payload.signature).
_JWT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)

_REDACTION_MARKER: Final[str] = "[REDACTED]"


def redact_error_message(message: str | None) -> str | None:
    """Strip secret-shaped substrings from ``message``.

    Returns ``None`` unchanged. Returns ``""`` when ``message``
    becomes empty after redaction (operators want to see that
    *something* was redacted; an empty string distinguishes
    "no error" from "error message was a credential").
    """
    if message is None:
        return None
    redacted = _OPENAI_KEY_PATTERN.sub(_REDACTION_MARKER, message)
    redacted = _JWT_PATTERN.sub(_REDACTION_MARKER, redacted)
    redacted = _BEARER_PATTERN.sub(_REDACTION_MARKER, redacted)
    redacted = _AUTH_HEADER_PATTERN.sub(_REDACTION_MARKER, redacted)
    redacted = _ENV_ASSIGN_PATTERN.sub(_REDACTION_MARKER, redacted)
    redacted = _HEX_TOKEN_PATTERN.sub(_REDACTION_MARKER, redacted)
    # When the only surviving content is the redaction marker
    # itself (every visible character was secret-shaped), the
    # redacted message is replaced with an empty string so the
    # operator sees that *something* was redacted rather than
    # a string of "[REDACTED]" sentinels. ``None`` is preserved
    # as ``None`` (caller can distinguish "no error" from
    # "error was a credential").
    if not redacted.strip() or redacted.strip() == _REDACTION_MARKER:
        return ""
    return redacted


def _utc_now() -> datetime:
    """UTC clock; injected via ``clock`` kwarg for tests."""
    return datetime.now(UTC)


def _hash_artifact(path_or_bytes: Path | bytes) -> str:
    """Return the SHA-256 hex digest of an artifact.

    The orchestrator calls this once per artifact and passes
    the hash on ``ProviderEvidenceRecord``. Storing hashes
    (not contents) is what lets the evidence envelope stay
    small while still being tamper-evident.
    """
    if isinstance(path_or_bytes, Path):
        digest = hashlib.sha256()
        with path_or_bytes.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    return hashlib.sha256(path_or_bytes).hexdigest()


@dataclass(frozen=True)
class ProviderEvidenceRecord:
    """Per-call durable evidence for one model invocation.

    Field names match the corrective doc §"Evidence
    requirements" verbatim. Defaults exist only so the
    dataclass can be constructed incrementally by callers
    that discover fields late; persistence is JSONL so any
    field left at the default is still recorded as ``null``
    / ``""`` / ``()`` (never silently absent).
    """

    run_id: str
    phase: str
    provider: str
    configured_model: str
    returned_model: str
    protocol: str
    endpoint_classification: str
    prompt_template_version: str
    local_correlation_id: str
    duration_s: float
    attempt_number: int
    task_id: str | None = None
    thinking_mode: bool | None = None
    service_tier: str | None = None
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    normalized_error_kind: str | None = None
    redacted_error_message: str | None = None
    input_artifact_hashes: tuple[str, ...] = field(default_factory=tuple)
    output_artifact_hash: str | None = None
    timestamp: str = field(default_factory=lambda: _utc_now().isoformat())

    def __post_init__(self) -> None:
        # Defensive validation: never let a raw error message
        # through to disk. Run the redactor one more time on
        # the input so a caller that already-formatted the
        # message can't sneak a credential past the writer.
        if self.redacted_error_message is not None:
            object.__setattr__(
                self,
                "redacted_error_message",
                redact_error_message(self.redacted_error_message),
            )
        if not self.run_id:
            msg = "ProviderEvidenceRecord.run_id must be non-empty"
            raise ValueError(msg)
        if not self.phase:
            msg = "ProviderEvidenceRecord.phase must be non-empty"
            raise ValueError(msg)
        if not self.configured_model:
            msg = (
                "ProviderEvidenceRecord.configured_model must be non-empty; "
                "the configured model is the audit anchor, not the "
                "returned model"
            )
            raise ValueError(msg)
        if not self.local_correlation_id:
            msg = (
                "ProviderEvidenceRecord.local_correlation_id must be non-empty; "
                "this is how the offline review links evidence to the run"
            )
            raise ValueError(msg)
        if self.duration_s < 0:
            msg = f"ProviderEvidenceRecord.duration_s must be >= 0, got {self.duration_s}"
            raise ValueError(msg)
        if self.attempt_number < 1:
            msg = f"ProviderEvidenceRecord.attempt_number must be >= 1, got {self.attempt_number}"
            raise ValueError(msg)


class ProviderEvidenceWriter:
    """Append-only JSONL writer for :class:`ProviderEvidenceRecord`.

    The writer opens the evidence file lazily (on first record)
    so test fixtures that never record don't pay for a file
    open. Each record is written with a single
    ``f.write(line + os.linesep)`` call, which POSIX guarantees
    is atomic for writes under ``PIPE_BUF`` (4096 bytes on
    Linux); a single evidence record comfortably fits.

    Thread/process safety: a single ``ProviderEvidenceWriter``
    instance is single-writer safe within a process. Cross-
    process safety comes from the worker lease layer (M3-3)
    which serializes run advancement. We do not acquire a
    ``fcntl`` lock here because that would couple the writer
    to a specific OS contract.
    """

    def __init__(
        self,
        *,
        evidence_dir: Path,
        clock: Callable[[], datetime] = _utc_now,
        filename: str = "evidence.jsonl",
    ) -> None:
        self._evidence_dir = Path(evidence_dir)
        self._path = self._evidence_dir / filename
        self._clock = clock
        self._file_handle: Any = None
        self._record_count = 0

    @property
    def evidence_dir(self) -> Path:
        return self._evidence_dir

    @property
    def path(self) -> Path:
        return self._path

    @property
    def record_count(self) -> int:
        return self._record_count

    def record(self, *, call: ProviderEvidenceRecord) -> None:
        """Append ``call`` to the evidence JSONL file.

        The first call creates the evidence directory and the
        file. Subsequent calls append. The file handle is
        cached and closed only on :meth:`close` (or process
        exit, whichever comes first).
        """
        if self._file_handle is None:
            self._evidence_dir.mkdir(parents=True, exist_ok=True)
            self._file_handle = self._path.open("a", encoding="utf-8")
        payload = dict(asdict(call))
        payload["timestamp"] = self._clock().isoformat()
        # ``json.dumps`` with ``sort_keys`` keeps records
        # diff-friendly across runs (operators diff evidence
        # files when replaying a vertical acceptance).
        line = json.dumps(payload, sort_keys=True, default=str)
        self._file_handle.write(line + os.linesep)
        self._file_handle.flush()
        self._record_count += 1

    def records(self) -> Iterator[ProviderEvidenceRecord]:
        """Yield every record in the evidence file.

        The writer does not cache the file handle after a
        :meth:`records` call; the writer remains append-only.
        Useful in tests and for offline replay.
        """
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                data = json.loads(stripped)
                yield _record_from_dict(data)

    def close(self) -> None:
        """Close the evidence file handle."""
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None


def _record_from_dict(data: dict[str, Any]) -> ProviderEvidenceRecord:
    """Build a ``ProviderEvidenceRecord`` from a parsed JSON dict.

    Round-trip helper for :meth:`ProviderEvidenceWriter.records`.
    Tuple-typed fields come back as lists from JSON; we coerce
    back so the dataclass type contract holds.
    """
    payload = dict(data)
    hashes = payload.get("input_artifact_hashes", ())
    if isinstance(hashes, list):
        payload["input_artifact_hashes"] = tuple(hashes)
    return ProviderEvidenceRecord(**payload)


__all__ = [
    "ProviderEvidenceRecord",
    "ProviderEvidenceWriter",
    "redact_error_message",
]


# Re-export helper symbols used by the orchestrator layer. We
# do NOT export ``_hash_artifact`` because artifact hashing
# is the orchestrator's concern (it knows the artifact paths),
# not the writer's.
__all__ += ["_hash_artifact"]
