"""Secret scrubber for trace persistence (Cluster E, story E6).

The :class:`SecretRedactor` applies a battery of regex patterns to
arbitrary text and replaces any matches with the
:data:`REDACTION_SENTINEL`. It is intentionally conservative: a false
positive (over-redaction) is cheap; a false negative leaks a secret.

Patterns covered:

- Telegram bot tokens (``1234567890:AABBCC...``)
- OpenAI project keys (``sk-...``, ``sk-proj-...``)
- Anthropic API keys (``sk-ant-...``)
- GitHub personal access tokens (``ghp_...``, ``ghs_...``)
- AWS access key IDs (``AKIA...``)
- Generic ``password=...`` / ``token=...`` / ``api_key=...`` assignments
- ``Basic <base64>`` auth header values

The redactor also walks dicts recursively
(:meth:`SecretRedactor.redact_dict`) so structured payloads can be
scrubbed before JSON serialisation.
"""

from __future__ import annotations

import re
from typing import Any

#: Stable sentinel string used to mark scrubbed values.
#: Operators grep for this token in incident response.
REDACTION_SENTINEL = "***REDACTED***"


# Each pattern is applied in order; the sentinel replaces the matched
# substring. Patterns are pre-compiled once at import time.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Telegram bot tokens (digits:base64); accepts /bot<token> prefix.
    re.compile(r"\d{6,}:[A-Za-z0-9_\-]{20,}"),
    # OpenAI project / classic keys.
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    # Anthropic API keys (sk-ant-...).
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    # GitHub personal access tokens.
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"ghs_[A-Za-z0-9]{20,}"),
    # AWS access key IDs.
    re.compile(r"AKIA[A-Z0-9]{12,}"),
    # Generic password= / token= / api_key= assignments; captures the
    # value up to the next whitespace or quote.
    re.compile(r"(?i)(password|token|api_key|apikey|secret)\s*=\s*['\"]?([^'\"\s]+)"),
    # Authorization: Basic <base64>.
    re.compile(r"(?i)Authorization:\s*Basic\s+[A-Za-z0-9+/=]{4,}"),
    # Bearer <long token> — generic, may over-match but never leaks.
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
)


class SecretRedactor:
    """Scrubs common secret patterns from arbitrary text or dicts.

    The redactor is stateless; every call is independent. This keeps
    it cheap to instantiate per request and safe to share across
    threads.
    """

    __slots__ = ()

    def redact(self, text: str) -> str:
        """Return ``text`` with all known secret patterns replaced.

        Empty / None-safe: returns the input unchanged when it is not
        a non-empty string.
        """
        if not text:
            return text
        out = text
        for pattern in _PATTERNS:
            out = pattern.sub(REDACTION_SENTINEL, out)
        return out

    def redact_dict(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``payload`` with all string values redacted.

        Nested dicts are walked recursively. Non-string scalars are
        preserved as-is. Lists of strings are scrubbed element-wise.
        """
        return _walk_dict(payload, self)

    def redact_value(self, value: Any) -> Any:
        """Redact a single value of arbitrary type."""
        return _walk(value, self)


# ---------------------------------------------------------------------------
# Internal walk helpers
# ---------------------------------------------------------------------------


def _walk_dict(d: dict[str, Any], redactor: SecretRedactor) -> dict[str, Any]:
    return {k: _walk(v, redactor) for k, v in d.items()}


def _walk(value: Any, redactor: SecretRedactor) -> Any:
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, dict):
        return _walk_dict(value, redactor)
    if isinstance(value, (list, tuple)):
        scrubbed = [_walk(v, redactor) for v in value]
        return type(value)(scrubbed)  # preserve list/tuple
    return value
