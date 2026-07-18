"""Tests for the seharness validate-config CLI command.

Slice 1, Behavior 4: Typer-based CLI command that loads configuration and
prints either a success line (text mode) or a JSON object (--format json).
Exits 0 on success, non-zero on validation failure.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from seharness.cli import app

runner = CliRunner()


class TestValidateConfigHelp:
    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_validate_config_help(self) -> None:
        result = runner.invoke(app, ["validate-config", "--help"])
        assert result.exit_code == 0


class TestValidateConfigTextMode:
    def test_valid_empty_config_returns_success(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", raising=False)
        result = runner.invoke(app, ["validate-config"])
        assert result.exit_code == 0, result.stdout
        assert "valid" in result.stdout.lower()

    def test_valid_yaml_returns_success(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", raising=False)
        cfg = tmp_path / "harness.yaml"
        cfg.write_text("harness:\n  artifact_root: /tmp/custom\n")
        result = runner.invoke(app, ["validate-config", "--repo-yaml", str(cfg)])
        assert result.exit_code == 0, result.stdout

    def test_invalid_yaml_returns_nonzero(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", raising=False)
        cfg = tmp_path / "harness.yaml"
        cfg.write_text("unknown_top_level_key: value\n")
        result = runner.invoke(app, ["validate-config", "--repo-yaml", str(cfg)])
        assert result.exit_code != 0


class TestValidateConfigJsonOutput:
    def test_json_output_is_valid_json(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", raising=False)
        result = runner.invoke(app, ["validate-config", "--format", "json"])
        assert result.exit_code == 0, result.stdout
        # stdout must be valid JSON
        data = json.loads(result.stdout)
        assert data["status"] == "valid"
        assert "config" in data

    def test_json_output_on_failure_contains_error(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SEHARNESS_HARNESS__ARTIFACT_ROOT", raising=False)
        cfg = tmp_path / "harness.yaml"
        cfg.write_text("garbage_key: 1\n")
        result = runner.invoke(
            app, ["validate-config", "--repo-yaml", str(cfg), "--format", "json"]
        )
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data["status"] == "invalid"
        assert "error" in data
