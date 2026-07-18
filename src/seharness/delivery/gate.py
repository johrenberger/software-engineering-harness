"""Local validation gate. Per SPEC §'Slice 9 RED bullet 4'.

Failed local validation MUST block PR creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class GateResult:
    """Outcome of a single gate."""

    gate_id: str
    passed: bool
    output: str


class GateFailureError(RuntimeError):
    """Raised when one or more gates fail."""

    def __init__(self, failed_gate_ids: tuple[str, ...], outputs: dict[str, str]):
        self.failed_gate_ids = failed_gate_ids
        self.outputs = outputs
        super().__init__(f"gates failed: {list(failed_gate_ids)}; outputs: {outputs}")


@runtime_checkable
class GateRunner(Protocol):
    """A single validation gate (ruff, mypy, pytest, etc.)."""

    def run(self, repo_root: Path) -> GateResult: ...


class LocalValidationGate:
    """Runs a sequence of GateRunners. Short-circuits on first failure."""

    def __init__(
        self,
        *,
        runners: tuple[GateRunner, ...] = (),
        raise_on_failure: bool = False,
    ) -> None:
        self._runners = runners
        self._raise = raise_on_failure

    def run(self, repo_root: Path) -> GateResult:
        outputs: dict[str, str] = {}
        failed: list[str] = []
        for runner in self._runners:
            result = runner.run(repo_root)
            outputs[result.gate_id] = result.output
            if not result.passed:
                failed.append(result.gate_id)
                if self._raise:
                    raise GateFailureError(failed_gate_ids=tuple(failed), outputs=outputs)
                return GateResult(
                    gate_id=result.gate_id,
                    passed=False,
                    output=result.output,
                )
        if failed:
            return GateResult(gate_id=failed[0], passed=False, output=outputs[failed[0]])
        return GateResult(gate_id="aggregate", passed=True, output="")
