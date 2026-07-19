"""Application service Protocol for SPEC §'Slice 11: Telegram ingress'.

Telegram handlers call into ``ApplicationService`` — the SAME
application service the CLI calls (per SPEC §'12. Telegram must call
the same application service as the CLI.').

Slice 12 wires the real implementation. Slice 11 ships the Protocol
+ ``FeatureRequest`` model + ``StubApplicationService`` for tests.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class FeatureRequest(BaseModel):
    """Frozen feature-request contract shared by CLI and Telegram.

    Pydantic frozen + extra=forbid per slice 5 style.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_url: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ApplicationService(Protocol):
    """Protocol for the application service.

    Slice 12 wires the real impl. The Telegram layer depends ONLY on
    this Protocol — there is no direct coupling to the controller or
    the CLI.
    """

    def feature_request(self, request: FeatureRequest) -> object: ...  # pragma: no cover

    def status(self, run_id: str) -> object: ...  # pragma: no cover

    def runs(self) -> tuple[str, ...]: ...  # pragma: no cover

    def resume(self, run_id: str) -> object: ...  # pragma: no cover

    def cancel(self, run_id: str) -> object: ...  # pragma: no cover

    def pr_status(self, run_id: str) -> object: ...  # pragma: no cover
