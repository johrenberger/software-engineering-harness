"""RED tests for behavior 05: Normalized failures across adapters.

Per SPEC §10: 'timeout and provider failure produce normalized results'.
Per SPEC §6: ModelResponse must preserve `error information`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from seharness.domain.enums import RoutingRole
from seharness.models import (
    FakeModelAdapter,
    ModelError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


def _req(prompt: str = "x") -> ModelRequest:
    return ModelRequest(role=RoutingRole.PLANNING, prompt=prompt)


class TestModelErrorShape:
    def test_required_fields(self) -> None:
        err = ModelError(kind="timeout", message="too slow")
        assert err.kind == "timeout"
        assert err.message == "too slow"

    def test_optional_retryable_field(self) -> None:
        err = ModelError(kind="timeout", message="x", retryable=True)
        assert err.retryable is True

    def test_default_retryable_is_false(self) -> None:
        err = ModelError(kind="provider_failure", message="x")
        assert err.retryable is False

    def test_error_kinds_are_canonical(self) -> None:
        """Per SPEC the normalized error kinds are limited to the four canonical
        failure modes: timeout, provider_failure, malformed_output, auth."""
        canonical = {"timeout", "provider_failure", "malformed_output", "auth"}
        # Every ModelError we construct must use a canonical kind.
        for kind in canonical:
            err = ModelError(kind=kind, message="m")
            assert err.kind == kind

    def test_error_kind_rejects_unknown_value(self) -> None:
        """The contract: the `kind` field is a Literal — non-canonical values
        must be rejected at construction time."""
        with pytest.raises(ValidationError):
            ModelError(kind="totally-unknown-kind", message="m")  # type: ignore[arg-type]


class TestTimeoutNormalization:
    def test_timeout_is_retryable(self, tmp_path: Any) -> None:
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        from pathlib import Path
        import json

        Path(fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"slow": {"timeout": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir, timeout_seconds=0.0)
        resp = adapter.invoke(_req("slow"))
        assert resp.error is not None
        assert resp.error.kind == "timeout"
        assert resp.error.retryable is True

    def test_timeout_records_duration(self, tmp_path: Any) -> None:
        """The response must still record duration even when the call failed."""
        from pathlib import Path
        import json

        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        Path(fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"slow": {"timeout": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir, timeout_seconds=0.0)
        resp = adapter.invoke(_req("slow"))
        assert resp.duration_s >= 0.0


class TestProviderFailureNormalization:
    def test_provider_failure_is_not_retryable_via_repair(
        self, tmp_path: Any
    ) -> None:
        """Provider-level failures should not be silently retried by the
        structured-output repair step. They surface as a non-repairable
        error so the router decides whether to fall back."""
        from pathlib import Path
        import json

        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        Path(fixture_dir / "fixtures.json").write_text(
            json.dumps(
                {"prompts": {"boom": {"provider_error": "internal"}}}
            ),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req("boom"))
        assert resp.error is not None
        assert resp.error.kind == "provider_failure"
        # The response must NOT request repair — provider failures are
        # the router's decision to make (fallback to a different model).
        assert resp.requires_repair is False


class TestMalformedOutputNormalization:
    def test_malformed_output_triggers_repair_flag(
        self, tmp_path: Any
    ) -> None:
        from pathlib import Path
        import json

        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        Path(fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"bad": {"malformed": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req("bad"))
        assert resp.error is not None
        assert resp.error.kind == "malformed_output"
        assert resp.requires_repair is True


class TestResponseShapeContract:
    def test_response_shape_carries_error_and_parsed(self) -> None:
        resp = ModelResponse(
            provider="minimax",
            model="minimax-M3",
            parsed={"a": 1},
            usage=ModelUsage(input_tokens=1, output_tokens=1),
            error=None,
            requires_repair=False,
        )
        assert resp.provider == "minimax"
        assert resp.model == "minimax-M3"
        assert resp.parsed == {"a": 1}
        assert resp.error is None
        assert resp.requires_repair is False
        assert resp.duration_s >= 0.0
        assert resp.files_changed == ()

    def test_response_default_files_changed_is_empty_tuple(self) -> None:
        resp = ModelResponse(
            provider="codex",
            model="codex-stub",
            parsed=None,
            error=ModelError(kind="provider_failure", message="x"),
        )
        assert resp.files_changed == ()

    def test_response_rejects_extra_fields(self) -> None:
        """The contract forbids extra fields — adapter authors cannot
        smuggle transport-specific metadata into the canonical response."""
        with pytest.raises(ValidationError):
            ModelResponse(
                provider="minimax",
                model="minimax-M3",
                parsed={},
                transport_metadata={"x": 1},  # type: ignore[call-arg]
            )

    def test_response_with_garbage_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelResponse(
                provider="not-a-provider",  # type: ignore[arg-type]
                model="x",
                parsed=None,
            )
