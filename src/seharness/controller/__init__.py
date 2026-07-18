"""Controller package — production wiring for OpenClaw packaging (slice 12).

SPEC §'21. OpenClaw packaging' — this package contains:
- ``RunLedger`` — in-memory record of feature runs.
- ``RunRecord`` / ``RunState`` — frozen Pydantic models.
- ``ControllerApplicationService`` — production impl of slice 11's
  ApplicationService Protocol. Dispatches to TaskExecutionService for
  /feature and CiMonitor for /pr.
- ``ApplicationServiceFactory`` — builds ApplicationService from
  controller.yaml (B2 = factory DI).
- ``Pauser`` / ``Resumer`` Protocols + Stub impls for operator skills.

NO workflow logic lives here; this is the wiring layer.
"""

from __future__ import annotations

from .application_service import (
    ControllerApplicationService,
    FeatureExecutor,
    StubFeatureExecutor,
)
from .config import ApplicationServiceFactory, ControllerConfig, ControllerConfigError
from .pause_resume import Pauser, Resumer, StubPauser, StubResumer
from .run_ledger import RunLedger, RunRecord, RunState

__all__ = [
    "ApplicationServiceFactory",
    "ControllerApplicationService",
    "ControllerConfig",
    "ControllerConfigError",
    "FeatureExecutor",
    "Pauser",
    "Resumer",
    "RunLedger",
    "RunRecord",
    "RunState",
    "StubFeatureExecutor",
    "StubPauser",
    "StubResumer",
]
