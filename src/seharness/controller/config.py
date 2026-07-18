"""ApplicationServiceFactory — builds ApplicationService from controller.yaml.

Per SPEC §'21. OpenClaw packaging' Q2=B2 — factory DI driven by
controller.yaml. The factory validates config and produces a
production ``ControllerApplicationService`` (or ``StubApplicationService``
when ``stub`` is selected per service).

The config is intentionally minimal:
- ``services`` is a mapping of slot-name → impl-name.
- Valid impl-names: ``stub`` or ``controller``.

Unknown impl-names raise ``ControllerConfigError``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..telegram.service import ApplicationService
from .run_ledger import RunLedger

if TYPE_CHECKING:
    from ..ci.monitor import CiMonitor
    from ..execution.service import TaskExecutionService


class ControllerConfigError(ValueError):
    """Raised when controller.yaml is malformed or violates invariants."""


class ControllerConfig(BaseModel):
    """Frozen controller config.

    Invariants:
    - ``services`` is a non-empty mapping.
    - Each value is one of the registered impl aliases.
    - Unknown keys rejected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    services: dict[str, str] = Field(min_length=1)

    @field_validator("services")
    @classmethod
    def _validate_services(cls, value: dict[str, str]) -> dict[str, str]:
        valid_slots = frozenset({"feature", "status", "runs", "resume", "cancel", "pr"})
        for slot in value:
            if slot not in valid_slots:
                raise ControllerConfigError(f"unknown service slot: {slot}")
        return value

    _VALID_IMPLS: ClassVar[frozenset[str]] = frozenset({"stub", "controller"})
    _VALID_SLOTS: ClassVar[frozenset[str]] = frozenset(
        {"feature", "status", "runs", "resume", "cancel", "pr"}
    )

    @classmethod
    def _validate(cls, value: dict[str, Any]) -> dict[str, Any]:
        services = value.get("services")
        if not isinstance(services, dict) or not services:
            raise ControllerConfigError("services must be a mapping with at least one entry")
        for slot, impl in services.items():
            if slot not in cls._VALID_SLOTS:
                raise ControllerConfigError(f"unknown service slot: {slot}")
            if not isinstance(impl, str):
                raise ControllerConfigError(
                    f"service value for {slot!r} must be a string, got {type(impl).__name__}"
                )
            if impl not in cls._VALID_IMPLS:
                raise ControllerConfigError(
                    f"unknown service implementation {impl!r} for {slot!r}; "
                    f"valid: {sorted(cls._VALID_IMPLS)}"
                )
        return value


def _build_controller_config(raw: dict[str, Any]) -> ControllerConfig:
    try:
        validated = ControllerConfig._validate(raw)
        return ControllerConfig.model_validate(validated)
    except ValidationError as exc:
        # Translate pydantic errors into ControllerConfigError.
        msg = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ControllerConfigError(msg) from exc


class ApplicationServiceFactory:
    """Builds an ApplicationService from controller.yaml.

    Usage:
        factory = ApplicationServiceFactory.from_yaml(Path("controller.yaml"))
        service = factory.build(task_executor=..., ci_monitor=..., run_ledger=...)

    If no YAML is provided, ``default()`` returns a factory that
    produces ``StubApplicationService`` (slice 11 default — fail-safe
    for tests).
    """

    def __init__(self, *, config: ControllerConfig | None) -> None:
        self._config = config

    @classmethod
    def default(cls) -> ApplicationServiceFactory:
        """Factory that builds StubApplicationService (no config)."""
        return cls(
            config=ControllerConfig(
                services={
                    "feature": "stub",
                    "status": "stub",
                    "runs": "stub",
                    "resume": "stub",
                    "cancel": "stub",
                    "pr": "stub",
                }
            )
        )

    @classmethod
    def from_yaml(cls, path: Path) -> ApplicationServiceFactory:
        if not path.exists():
            raise ControllerConfigError(f"controller config not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ControllerConfigError(f"invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ControllerConfigError("controller config must be a YAML mapping at the top level")
        # Reject unknown top-level keys explicitly.
        known = {"services"}
        unknown = set(raw) - known
        if unknown:
            raise ControllerConfigError(f"unknown field(s) at top level: {sorted(unknown)}")
        return cls(config=_build_controller_config(raw))

    def build(
        self,
        *,
        task_executor: TaskExecutionService | None = None,
        ci_monitor: CiMonitor | None = None,
        run_ledger: RunLedger | None = None,
    ) -> ApplicationService:
        """Build the ApplicationService per the config.

        Wiring rules:
        - All ``stub`` slots → ``StubApplicationService`` returned.
        - Any ``controller`` slot → ``ControllerApplicationService`` built
          from injected dependencies. Raises if dependencies missing.
        """
        if self._config is None:
            return _StubFactoryProxy()
        uses_controller = any(v == "controller" for v in self._config.services.values())
        if not uses_controller:
            return _StubFactoryProxy()
        if task_executor is None or ci_monitor is None or run_ledger is None:
            raise ControllerConfigError(
                "controller implementation requires task_executor, ci_monitor, run_ledger"
            )
        from .application_service import (  # noqa: PLC0415
            ControllerApplicationService,
            FeatureExecutor,
        )

        executor: FeatureExecutor = task_executor  # type: ignore[assignment]
        return ControllerApplicationService(
            task_executor=executor,
            ci_monitor=ci_monitor,
            run_ledger=run_ledger,
        )


class _StubFactoryProxy:
    """Forwards all attribute access to a ``StubApplicationService`` instance.

    Used by ``ApplicationServiceFactory.build()`` when no real controller
    dependencies are injected. Tests assert ``"Stub" in type(service).__name__``.
    """

    def __init__(self) -> None:
        from ..telegram.handlers import StubApplicationService  # noqa: PLC0415

        self._impl = StubApplicationService()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)
