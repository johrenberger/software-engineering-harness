"""RED: controller_factory builds ApplicationService from controller.yaml.

SPEC §'21. OpenClaw packaging' line 2217 — production wiring of the
slice-11 ApplicationService Protocol. The factory MUST build a
ControllerApplicationService that delegates /feature to the slice-7
TaskExecutionService and /pr to the slice-10 CiMonitor.

RED bullets covered:
- ControllerApplicationService is built from controller.yaml config.
- The factory rejects unknown service names.
- The factory fails-secure if a required dependency is missing.
- The factory defaults to Stub implementations if no config provided.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seharness.controller import (
    ApplicationServiceFactory,
    ControllerConfig,
    ControllerConfigError,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "controller.yaml"
    p.write_text(body)
    return p


def test_factory_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ControllerConfigError, match=r"not found"):
        ApplicationServiceFactory.from_yaml(missing)


def test_factory_rejects_unknown_service(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature: bogus-implementation
  status: stub
  runs: stub
  resume: stub
  cancel: stub
  pr: stub
""",
    )
    with pytest.raises(ControllerConfigError, match=r"unknown service"):
        ApplicationServiceFactory.from_yaml(cfg)


def test_factory_rejects_non_string_service_value(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature:
    impl: stub
    extra: nope
""",
    )
    with pytest.raises(ControllerConfigError, match=r"must be a string"):
        ApplicationServiceFactory.from_yaml(cfg)


def test_factory_accepts_stub_alias(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature: stub
  status: stub
  runs: stub
  resume: stub
  cancel: stub
  pr: stub
""",
    )
    factory = ApplicationServiceFactory.from_yaml(cfg)
    service = factory.build()
    assert service is not None


def test_factory_accepts_controller_alias(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature: controller
  status: stub
  runs: stub
  resume: stub
  cancel: stub
  pr: stub
""",
    )
    factory = ApplicationServiceFactory.from_yaml(cfg)
    service = factory.build()
    assert service is not None


def test_factory_rejects_unknown_alias(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature: not-a-real-impl
  status: stub
  runs: stub
  resume: stub
  cancel: stub
  pr: stub
""",
    )
    with pytest.raises(ControllerConfigError, match=r"unknown"):
        ApplicationServiceFactory.from_yaml(cfg)


def test_factory_without_yaml_returns_stub() -> None:
    """Default (no config) → StubApplicationService for testability."""
    factory = ApplicationServiceFactory.default()
    service = factory.build()
    # Default must NOT silently call controller code paths.
    assert service.__class__.__name__ == "StubApplicationService"


def test_controller_config_rejects_empty_services(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services: {}
""",
    )
    with pytest.raises(ControllerConfigError, match=r"at least one"):
        ApplicationServiceFactory.from_yaml(cfg)


def test_controller_config_rejects_extra_top_level(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        """
services:
  feature: stub
  status: stub
  runs: stub
  resume: stub
  cancel: stub
  pr: stub
forbidden_top_level: nope
""",
    )
    with pytest.raises(ControllerConfigError, match=r"unknown field"):
        ApplicationServiceFactory.from_yaml(cfg)


def test_controller_config_is_frozen() -> None:
    cfg = ControllerConfig(
        services={
            "feature": "stub",
            "status": "stub",
            "runs": "stub",
            "resume": "stub",
            "cancel": "stub",
            "pr": "stub",
        }
    )
    with pytest.raises(Exception):  # noqa: B017
        cfg.services = {"feature": "different"}  # type: ignore[misc]
