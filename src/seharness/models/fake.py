"""FakeModelAdapter for deterministic workflow tests (slice 4).

Per SPEC §10 the fake adapter must support:

- load response fixtures
- optionally simulate malformed output
- optionally simulate timeout
- optionally simulate provider failure
- optionally write controlled source-code changes

This implementation reads a single JSON file at ``fixtures_dir/fixtures.json``
with the following shape::

    {
        "prompts": {
            "<prompt-text>": {
                "text": "...",                # raw model output
                "parsed": {...},              # optional pre-parsed payload
                "usage": {"input_tokens": int, "output_tokens": int},
                "timeout": bool,              # simulate timeout
                "provider_error": str,        # simulate provider failure
                "malformed": bool,            # simulate malformed structured output
                "write_changes": [            # controlled side-effect
                    {"path": "src/foo.py", "content": "x = 1\\n"}
                ]
            }
        }
    }

If a prompt is not in the fixtures the adapter raises ``LookupError`` so
tests cannot silently fall back to a default — every fixture must be
explicit.

Side-effect writes are sandboxed to ``working_dir`` via ``Path.resolve()``
and a ``Path.is_relative_to`` check; any write that escapes the working
directory raises ``PermissionError`` rather than corrupting the host
filesystem.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from seharness.domain.enums import ProviderKind, ProviderName
from seharness.domain.requests import ModelRequest
from seharness.domain.results import ModelError, ModelResponse, ModelUsage
from seharness.models.base import ModelAdapter

_FAKE_PROVIDER: ProviderName = ProviderName.MINIMAX
_FAKE_MODEL_NAME: str = "fake/minimax-M3"


class FakeModelAdapter(ModelAdapter):
    """Deterministic fake adapter for workflow tests."""

    provider: ProviderName = _FAKE_PROVIDER
    kind: ProviderKind = ProviderKind.FAKE

    def __init__(
        self,
        *,
        fixtures_dir: Path,
        working_dir: Path | None = None,
        model_name: str = _FAKE_MODEL_NAME,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._working_dir = Path(working_dir) if working_dir is not None else None
        # Resolve once at construction so per-iteration Path.resolve() does
        # not produce mypy "unreachable" warnings for the containment check.
        self._working_dir_resolved: Path | None = (
            self._working_dir.resolve() if self._working_dir is not None else None
        )
        self._model_name = model_name
        self._timeout_seconds = float(timeout_seconds)
        self._fixtures = self._load_fixtures()

    def _load_fixtures(self) -> dict[str, Any]:
        path = self._fixtures_dir / "fixtures.json"
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "prompts" not in data:
            msg = f"fixtures file must be a JSON object with a 'prompts' key: {path}"
            raise ValueError(msg)
        return data

    def _lookup(self, prompt: str) -> dict[str, Any]:
        prompts = self._fixtures.get("prompts", {})
        if prompt not in prompts:
            msg = f"no fake fixture for prompt: {prompt!r}"
            raise LookupError(msg)
        fixture: Any = prompts[prompt]
        if not isinstance(fixture, dict):
            msg = f"fixture for prompt {prompt!r} is not a JSON object"
            raise TypeError(msg)
        result: dict[str, Any] = fixture
        return result

    def invoke(self, request: ModelRequest) -> ModelResponse:
        start = time.monotonic()
        fixture = self._lookup(request.prompt)

        # Optional controlled side-effect — must run before failure paths
        # so an attempt to escape the working dir is caught even when the
        # fixture also simulates a timeout or provider failure.
        files_changed = self._maybe_write_changes(fixture)

        # Simulate provider failure (returned, not raised).
        provider_error = fixture.get("provider_error")
        if provider_error is not None:
            duration = time.monotonic() - start
            return ModelResponse(
                provider=self.provider,
                model=self._model_name,
                parsed=None,
                error=ModelError(kind="provider_failure", message=str(provider_error)),
                requires_repair=False,
                files_changed=files_changed,
                duration_s=duration,
            )

        # Simulate timeout (returned, not raised).
        if fixture.get("timeout"):
            duration = time.monotonic() - start
            return ModelResponse(
                provider=self.provider,
                model=self._model_name,
                parsed=None,
                error=ModelError(
                    kind="timeout",
                    message=f"fake adapter simulated timeout after {self._timeout_seconds}s",
                    retryable=True,
                ),
                requires_repair=False,
                files_changed=files_changed,
                duration_s=duration,
            )

        # Simulate malformed structured output.
        if fixture.get("malformed"):
            duration = time.monotonic() - start
            return ModelResponse(
                provider=self.provider,
                model=self._model_name,
                parsed=None,
                error=ModelError(
                    kind="malformed_output",
                    message="fake adapter produced malformed structured output",
                ),
                requires_repair=True,
                files_changed=files_changed,
                duration_s=duration,
            )

        # Well-formed fixture — build the parsed payload.
        usage_data = fixture.get("usage")
        usage: ModelUsage | None
        if isinstance(usage_data, dict):
            usage = ModelUsage(
                input_tokens=int(usage_data.get("input_tokens", 0)),
                output_tokens=int(usage_data.get("output_tokens", 0)),
            )
        else:
            usage = None

        parsed: dict[str, Any]
        if "parsed" in fixture and isinstance(fixture["parsed"], dict):
            parsed = dict(fixture["parsed"])
        else:
            text_value = str(fixture.get("text", ""))
            parsed = {"text": text_value}

        duration = time.monotonic() - start
        return ModelResponse(
            provider=self.provider,
            model=self._model_name,
            parsed=parsed,
            raw_output=fixture.get("text"),
            usage=usage,
            error=None,
            requires_repair=False,
            files_changed=files_changed,
            duration_s=duration,
        )

    def _maybe_write_changes(self, fixture: dict[str, Any]) -> tuple[str, ...]:
        changes = fixture.get("write_changes")
        if not changes:
            return ()
        if self._working_dir is None:
            msg = "fixture declares write_changes but fake adapter has no working_dir configured"
            raise ValueError(msg)
        written: list[str] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            rel = change.get("path")
            content = change.get("content")
            if not isinstance(rel, str) or not isinstance(content, str):
                continue
            target = (self._working_dir / rel).resolve()
            working_root = self._working_dir_resolved
            if working_root is None or not target.is_relative_to(working_root):
                msg = f"write_change escapes working_dir: {rel}"
                raise PermissionError(msg)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
        return tuple(written)


__all__ = ["FakeModelAdapter"]
