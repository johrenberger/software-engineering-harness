"""RED tests for behavior 02: FakeModelAdapter.

Per SPEC §10: "Fake adapter must support deterministic workflow tests.
It should: load response fixtures, optionally simulate malformed output,
optionally simulate timeout, optionally simulate provider failure,
optionally write controlled source-code changes."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from seharness.domain.enums import ProviderKind, ProviderName, RoutingRole
from seharness.models import FakeModelAdapter, ModelRequest, ModelResponse


def _req(
    *,
    prompt: str = "hello",
    role: RoutingRole = RoutingRole.PLANNING,
    **extra: Any,
) -> ModelRequest:
    return ModelRequest(role=role, prompt=prompt, **extra)


class TestFakeAdapterMetadata:
    def test_provider_is_minimax_or_codex(self) -> None:
        # The fake adapter replaces one of the real providers. Per SPEC it must
        # participate in the same routing table.
        assert FakeModelAdapter.provider in {ProviderName.MINIMAX, ProviderName.CODEX}

    def test_kind_is_fake(self) -> None:
        assert FakeModelAdapter.kind == ProviderKind.FAKE


class TestFakeAdapterFixtureLoading:
    def test_loads_response_fixtures_from_directory(self, tmp_path: Path) -> None:
        """Spec: 'load response fixtures' — the adapter must read JSON fixtures
        keyed by request id (or prompt hash) and return deterministic responses."""
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        # Fixture keyed by exact prompt text (deterministic mapping).
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"hello": {"text": "world"}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req(prompt="hello"))
        assert isinstance(resp, ModelResponse)
        assert resp.parsed == {"text": "world"}
        assert resp.error is None

    def test_missing_fixture_raises_lookup_error(self, tmp_path: Path) -> None:
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {}}), encoding="utf-8"
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        with pytest.raises(LookupError):
            adapter.invoke(_req(prompt="absent"))


class TestFakeAdapterSimulatedFailures:
    def test_simulate_malformed_output(self, tmp_path: Path) -> None:
        """Spec: 'optionally simulate malformed output' — fixture can declare
        a 'malformed: true' payload so the caller experiences parse failure."""
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"bad": {"malformed": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req(prompt="bad"))
        assert resp.parsed is None
        assert resp.error is not None
        assert resp.error.kind in {"malformed_output", "parse_error", "invalid_structured_output"}

    def test_simulate_timeout_via_flag(self, tmp_path: Path) -> None:
        """Spec: 'optionally simulate timeout'."""
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"slow": {"timeout": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir, timeout_seconds=0.0)
        resp = adapter.invoke(_req(prompt="slow"))
        assert resp.error is not None
        assert resp.error.kind == "timeout"

    def test_simulate_provider_failure(self, tmp_path: Path) -> None:
        """Spec: 'optionally simulate provider failure'."""
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"boom": {"provider_error": "internal"}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req(prompt="boom"))
        assert resp.error is not None
        assert resp.error.kind == "provider_failure"

    def test_simulate_malformed_triggers_repair_recommendation(
        self, tmp_path: Path
    ) -> None:
        """When the fake produces malformed output, the response must recommend
        a one-shot repair so downstream callers can route to output_repair."""
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"bad": {"malformed": True}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req(prompt="bad"))
        # Either the response itself carries a repair recommendation, or
        # `requires_repair` is set so the router/repair step can act on it.
        assert resp.requires_repair is True


class TestFakeAdapterControlledSourceChange:
    def test_writes_controlled_source_change(self, tmp_path: Path) -> None:
        """Spec: 'optionally write controlled source-code changes'.

        The fake can declare a side-effect that creates/edits a file inside an
        explicit working directory. The adapter must:
        - write the file
        - record the change in the response metadata
        - never write outside the configured working directory
        """
        workdir = tmp_path / "work"
        workdir.mkdir()
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps(
                {
                    "prompts": {
                        "edit": {
                            "text": "ok",
                            "write_changes": [
                                {"path": "src/foo.py", "content": "x = 1\n"},
                            ],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(
            fixtures_dir=fixture_dir,
            working_dir=workdir,
        )
        resp = adapter.invoke(_req(prompt="edit"))
        target = workdir / "src" / "foo.py"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "x = 1\n"
        assert resp.files_changed == ("src/foo.py",)

    def test_writes_rejected_outside_working_directory(self, tmp_path: Path) -> None:
        workdir = tmp_path / "work"
        workdir.mkdir()
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps(
                {
                    "prompts": {
                        "evil": {
                            "text": "ok",
                            "write_changes": [
                                {
                                    "path": "../escape.py",
                                    "content": "BAD\n",
                                },
                            ],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(
            fixtures_dir=fixture_dir,
            working_dir=workdir,
        )
        with pytest.raises(PermissionError):
            adapter.invoke(_req(prompt="evil"))
        assert not (tmp_path / "escape.py").exists()


class TestFakeAdapterResponseShape:
    def test_response_records_provider_and_model(self, tmp_path: Path) -> None:
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps({"prompts": {"hi": {"text": "yo"}}}),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(
            fixtures_dir=fixture_dir, model_name="fake/minimax-M3"
        )
        resp = adapter.invoke(_req(prompt="hi"))
        assert resp.provider == adapter.provider
        assert resp.model == "fake/minimax-M3"
        assert resp.duration_s >= 0.0

    def test_response_records_usage_metadata(self, tmp_path: Path) -> None:
        fixture_dir = tmp_path / "fixtures"
        fixture_dir.mkdir()
        (fixture_dir / "fixtures.json").write_text(
            json.dumps(
                {
                    "prompts": {
                        "u": {
                            "text": "ok",
                            "usage": {"input_tokens": 5, "output_tokens": 7},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        adapter = FakeModelAdapter(fixtures_dir=fixture_dir)
        resp = adapter.invoke(_req(prompt="u"))
        assert resp.usage is not None
        assert resp.usage.input_tokens == 5
        assert resp.usage.output_tokens == 7
