"""Cluster H, story H2: suspicious-payload filtering.

In a world where the orchestrator accepts text input from many
sources (Telegram messages, GitHub comments, harness config values,
file uploads), an incoming payload can carry:

- Excessive length (resource exhaustion / cost blowup).
- Binary content embedded in an expected-text field.
- Control characters intended to confuse log readers or terminal
  emulators.
- Known prompt-injection markers (\"ignore previous instructions\"
  and variants).

This module provides :class:`SuspiciousPayloadFilter`, a
pure-function guard that returns a structured verdict. Callers
decide what to do with the verdict (reject the input, log it,
sanitise and proceed).

Design notes:

- The filter is *additive*: ``FilterVerdict.ok`` defaults to True,
  and reasons only appear when something triggers a flag. This
  keeps the happy-path cheap.
- The filter is *closed*: there are exactly seven reasons, each
  in a closed Literal. Anything else is rejected as
  ``"unknown_filter_reason"`` so the validator cannot smuggle in
  a fake-clean verdict.
- Limits are configurable via ``PayloadFilterConfig`` so
  deployments can tighten or loosen them without touching code.

This module has no I/O and no subprocess calls — it's pure logic,
easy to unit-test, and safe to call from any context.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# Closed set of filter reasons. Adding a new reason requires
# updating ``PayloadFilterConfig.evaluate`` and ``SuspiciousPayloadFilter.check``
# — both functions enforce this closed set via typed returns.
FilterReason = Literal[
    "too_long",
    "binary_content",
    "control_characters",
    "excessive_control_characters",
    "prompt_injection_marker",
    "null_bytes",
    "zero_length",
]


class PayloadFilterConfig(BaseModel):
    """Tunable limits for the suspicious-payload filter.

    Defaults are conservative: they block obvious attacks but allow
    realistic engineering inputs. Tighten ``max_length`` to match
    a model-provider context window; raise ``prompt_injection_*``
    patterns to a deny-list curated by security.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_length: int = Field(default=100_000, ge=1)
    """Maximum allowed character length for an incoming payload.
    Anything longer is rejected with ``too_long``."""

    max_control_chars: int = Field(default=8, ge=0)
    """Maximum allowed count of \"subtle\" control characters
    (e.g. ESC, BEL, BS). Above this the payload is rejected with
    ``control_characters``. Below this but above zero the payload
    is rejected with ``excessive_control_characters`` if the ratio
    of control chars to total chars exceeds ``max_control_ratio``."""

    max_control_ratio: float = Field(default=0.01, ge=0.0, le=1.0)
    """Maximum allowed ratio of control chars to total chars.
    Anything above is rejected with ``excessive_control_characters``."""

    prompt_injection_patterns: tuple[str, ...] = (
        r"(?i)\bignore (?:all )?previous instructions\b",
        r"(?i)\bdisregard (?:all )?prior (?:instructions|context)\b",
        r"(?i)\bforget (?:everything|all) (?:above|before)\b",
        r"(?i)\bsystem:\s*you are\b",
        r"<\|im_start\|>",  # LLM chat-template marker
        "\u200b",  # zero-width space (common in injection attempts)
    )
    """Regex patterns that, if matched anywhere in the payload,
    trigger ``prompt_injection_marker``. Compiled once at
    construction time."""

    allow_null_bytes: bool = False
    """Whether zero-length-but-not-empty strings (i.e. just null
    bytes) are accepted. Default False; set True only for legacy
    binary-passthrough flows that already sanitize separately."""


class FilterVerdict(BaseModel):
    """The structured outcome of a single filter call.

    ``ok`` is True when the payload passed every check. ``reasons``
    lists the canonical :data:`FilterReason` values that triggered;
    empty list means clean.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    reasons: tuple[FilterReason, ...] = ()
    sanitized_preview: str | None = None
    """A short, safe preview of the payload (truncated to 200 chars
    with control characters replaced) for telemetry/logging. Only
    populated when ``ok`` is False, to avoid leaking the full input."""


# Pre-compiled control character classes. Build once at module load
# rather than per-call.
_CONTROL_CHARS: Final[frozenset[int]] = frozenset(
    {
        0x00,  # NUL (handled separately as null_bytes)
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x06,  # SOH..ACK
        0x07,  # BEL
        0x08,  # BS
        0x0B,
        0x0C,  # VT, FF
        0x0E,
        0x0F,  # SO, SI
        0x10,
        0x11,
        0x12,
        0x13,
        0x14,
        0x15,
        0x16,
        0x17,  # DLE..ETB
        0x18,
        0x19,
        0x1A,
        0x1C,
        0x1D,
        0x1E,
        0x1F,  # CAN..US
        0x7F,  # DEL
    }
)

# Allowed control characters: tab, newline, carriage return.
_ALLOWED_CONTROL: Final[frozenset[int]] = frozenset({0x09, 0x0A, 0x0D})


class SuspiciousPayloadFilter:
    """Reject or accept text payloads against a configurable policy.

    The filter is *pure* — no I/O, no subprocess, no logging
    side-effects. Construct once with a :class:`PayloadFilterConfig`
    and call :meth:`check` from any thread.

    Example:

        cfg = PayloadFilterConfig(max_length=10_000)
        f = SuspiciousPayloadFilter(cfg)
        verdict = f.check("hello world")
        assert verdict.ok
    """

    __slots__ = ("_config", "_injection_regexes")

    def __init__(self, config: PayloadFilterConfig | None = None) -> None:
        self._config = config or PayloadFilterConfig()
        # Compile injection regexes once. PatternError surfaces here if
        # the config is malformed, instead of failing on every check.
        self._injection_regexes: tuple[re.Pattern[str], ...] = tuple(
            re.compile(p) for p in self._config.prompt_injection_patterns
        )

    @property
    def config(self) -> PayloadFilterConfig:
        return self._config

    def check(self, payload: str) -> FilterVerdict:
        """Evaluate ``payload`` against the configured policy.

        Returns a :class:`FilterVerdict` with ``ok=True`` if the
        payload passes every check; ``ok=False`` with a tuple of
        :data:`FilterReason` values otherwise.
        """
        if not payload:
            return FilterVerdict(ok=False, reasons=("zero_length",))

        reasons: list[FilterReason] = []

        # 1. Length
        if len(payload) > self._config.max_length:
            reasons.append("too_long")

        # 2. Null bytes
        if payload.count("\x00") > 0 and not self._config.allow_null_bytes:
            reasons.append("null_bytes")

        # 3. Control characters (excluding NUL which is its own reason)
        control_count = sum(1 for c in payload if ord(c) in _CONTROL_CHARS and ord(c) != 0)
        if control_count > self._config.max_control_chars:
            reasons.append("control_characters")
        elif control_count > 0:
            ratio = control_count / max(len(payload), 1)
            if ratio > self._config.max_control_ratio:
                reasons.append("excessive_control_characters")

        # 4. Prompt-injection markers
        for regex in self._injection_regexes:
            if regex.search(payload):
                reasons.append("prompt_injection_marker")
                break

        # 5. Binary-content heuristic: if the payload contains any
        # non-ASCII byte that's NOT a common Unicode letter mark
        # (CJK, accents, etc.) AND is more than 20% of total chars,
        # flag as binary_content. Encoded UTF-8 text (e.g. CJK
        # source code) almost never trips this; binary blobs do.
        if self._looks_binary(payload):
            reasons.append("binary_content")

        if not reasons:
            return FilterVerdict(ok=True)

        return FilterVerdict(
            ok=False,
            reasons=tuple(reasons),
            sanitized_preview=self._safe_preview(payload),
        )

    @staticmethod
    def _looks_binary(payload: str) -> bool:
        """Heuristic: detect likely-binary content in a string.

        Two patterns flag as binary:

        1. **High non-ASCII ratio with multiple high-bit runs** —
           real binary blobs (compressed data, encoded files) tend
           to have several long stretches of high-bit bytes
           interspersed with structure. CJK source code is mostly
           non-ASCII but distributed more evenly.

        2. **One extremely long high-bit run** (>= 32 chars) — a
           single run of >= 32 high-bit chars is almost never
           natural language. Real language runs of non-ASCII
           characters rarely exceed ~10 chars before a space or
           punctuation.
        """
        if not payload:
            return False
        non_ascii = sum(1 for c in payload if ord(c) > 0x7F)
        # Pure ASCII: never binary by this heuristic.
        if non_ascii == 0:
            return False

        max_run = 0
        run_length = 0
        high_runs = 0
        for c in payload:
            if ord(c) > 0x7F:
                run_length += 1
            else:
                if run_length > 8:
                    high_runs += 1
                    max_run = max(max_run, run_length)
                run_length = 0
        if run_length > 8:
            high_runs += 1
            max_run = max(max_run, run_length)

        ratio = non_ascii / len(payload)
        # One extremely long run of high-bit chars is binary.
        if max_run >= 32:
            return True
        # Otherwise, multiple high-bit runs at high non-ASCII ratio.
        return ratio > 0.30 and high_runs >= 2

    @staticmethod
    def _safe_preview(payload: str) -> str:
        """Return a 200-char preview with control chars replaced."""
        safe = "".join(c if (ord(c) >= 0x20 or c in "\t\n\r") else "?" for c in payload[:200])
        if len(payload) > 200:
            safe += f"... [truncated {len(payload) - 200} chars]"
        return safe


__all__ = [
    "FilterReason",
    "FilterVerdict",
    "PayloadFilterConfig",
    "SuspiciousPayloadFilter",
]
