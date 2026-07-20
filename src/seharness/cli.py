"""Typer-based CLI for the software engineering harness.

Slice 1 implements ``validate-config`` as a subcommand. Typer 0.27 only
creates a Group when an app has more than one registered command, so
we also register a stub ``run`` callback that prints "not implemented
yet" — this forces Group mode without changing the user-facing API and
makes it trivial to swap in the real ``run`` implementation in a
later slice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from seharness.config_loader import ConfigurationError, load_config

app = typer.Typer(
    name="seharness",
    help="Software Engineering Harness — framework-neutral Python harness for OpenClaw.",
    no_args_is_help=True,
    add_completion=False,
)


def _emit(data: dict[str, Any], fmt: str) -> None:
    """Emit a result dict either as a single-line summary or as JSON."""
    if fmt == "json":
        typer.echo(json.dumps(data, indent=2, sort_keys=True))
        return
    status = data.get("status", "unknown")
    if status == "valid":
        config = data.get("config", {})
        harness = config.get("harness", {}) if isinstance(config, dict) else {}
        artifact_root = harness.get("artifact_root", "?")
        typer.echo(f"configuration valid (artifact_root={artifact_root})")
    else:
        typer.echo(f"configuration INVALID: {data.get('error', '<no message>')}")


@app.command("validate-config")
def validate_config(
    repo_yaml: Path | None = typer.Option(
        None,
        "--repo-yaml",
        help="Path to repository harness.yaml. Defaults to ./harness.yaml.",
    ),
    local_yaml: Path | None = typer.Option(
        None,
        "--local-yaml",
        help="Path to local override file. Defaults to ./seharness.local.yaml.",
    ),
    no_env: bool = typer.Option(
        False,
        "--no-env",
        help="Ignore environment variables (useful for deterministic checks).",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Validate the merged harness configuration.

    Loads configuration from defaults, repository harness.yaml, local
    override, environment, and CLI-provided overrides. Exits 0 on
    success, non-zero on validation failure.
    """
    if output_format not in {"text", "json"}:
        typer.echo(f"unknown --format value: {output_format}", err=True)
        raise typer.Exit(code=2)
    if repo_yaml is None and Path("harness.yaml").exists():
        repo_yaml = Path("harness.yaml")
    if local_yaml is None and Path("seharness.local.yaml").exists():
        local_yaml = Path("seharness.local.yaml")
    try:
        config = load_config(
            repo_yaml=repo_yaml,
            local_yaml=local_yaml,
            include_env=not no_env,
        )
    except ConfigurationError as e:
        _emit({"status": "invalid", "error": str(e)}, output_format)
        raise typer.Exit(code=1) from e
    _emit({"status": "valid", "config": config.model_dump()}, output_format)


@app.command("run")
def run_command(
    repository: str = typer.Option(..., "--repository", help="Local path or git URL."),
    feature: str = typer.Option(..., "--feature", help="Feature description."),
    model: str = typer.Option(
        "fake", "--model", help="Implementation model: fake, minimax, codex."
    ),
    idempotency_key: str = typer.Option(
        "",
        "--idempotency-key",
        envvar="SEHARNESS_IDEMPOTENCY_KEY",
        help=(
            "Stable identifier for the logical request (Cluster E1). "
            "Re-runs with the same key dedupe to the existing run; "
            "collisions on a different run_id raise. Empty = no dedupe."
        ),
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Run a harness workflow for a feature.

    Cluster A: this invokes the canonical ``Orchestrator`` end-to-end.
    The ``--model`` flag is accepted but does not yet select a model
    adapter (Cluster F wires real adapters). Cluster E1 surfaces
    ``--idempotency-key`` so callers can pass a stable key for
    retries / replay.
    """
    if output_format not in {"text", "json"}:
        typer.echo(f"unknown --format value: {output_format}", err=True)
        raise typer.Exit(code=2)
    from seharness.controller.run_ledger import RunLedger  # noqa: PLC0415
    from seharness.orchestrator import Orchestrator  # noqa: PLC0415

    orchestrator = Orchestrator(run_ledger=RunLedger())
    result = orchestrator.start_run(
        feature_description=feature,
        repo_path=repository,
        idempotency_key=idempotency_key,
    )
    payload: dict[str, Any] = {
        "status": "ok" if result.terminal_state == "completed" else result.terminal_state,
        "run_id": result.run_id,
        "terminal_state": result.terminal_state,
        "events": len(result.events),
        "phases": [e.phase for e in result.events],
    }
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"run {result.run_id}: {result.terminal_state} ({len(result.events)} events)")
    if result.terminal_state != "completed":
        raise typer.Exit(code=1)


def main() -> None:
    """Console entry point for ``seharness``."""
    try:
        app(standalone_mode=True)
    except SystemExit as e:
        sys.exit(e.code)


if __name__ == "__main__":
    main()
