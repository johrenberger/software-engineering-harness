"""RED tests for behavior 01 — RepositoryProfile.

RepositoryProfile is the Pydantic v2 model that captures every fact the
inspector discovers about the target repository. It must:

* forbid unknown fields (``extra='forbid'``)
* be immutable after construction (``frozen=True``) so downstream code
  cannot mutate it mid-run
* default to safe empty values so callers can construct a profile and
  fill it incrementally during discovery
* expose all 13 fields listed in SPEC.md "Repository profile" section
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.repository.discovery import RepositoryProfile


def _new_profile(**overrides: object) -> RepositoryProfile:
    """Helper: build a profile with sensible defaults for tests."""
    defaults: dict[str, object] = {
        "name": "demo",
        "path": "/tmp/demo",
        "base_commit": "deadbeef",
        "python_version_constraint": ">=3.12",
        "package_manager": "uv",
        "source_roots": ("src",),
        "test_roots": ("tests",),
        "framework_indicators": (),
        "validation_commands": (),
        "ci_workflows": (),
        "architecture_summary": "",
        "conventions": (),
        "baseline_validation_status": "unknown",
    }
    defaults.update(overrides)
    return RepositoryProfile(**defaults)  # type: ignore[arg-type]


class TestRepositoryProfileRequiredFields:
    """All 13 fields from SPEC are present and readable."""

    def test_has_name(self) -> None:
        assert _new_profile().name == "demo"

    def test_has_path(self) -> None:
        assert _new_profile().path == "/tmp/demo"

    def test_has_base_commit(self) -> None:
        assert _new_profile().base_commit == "deadbeef"

    def test_has_python_version_constraint(self) -> None:
        assert _new_profile().python_version_constraint == ">=3.12"

    def test_has_package_manager(self) -> None:
        assert _new_profile().package_manager == "uv"

    def test_has_source_roots(self) -> None:
        assert _new_profile().source_roots == ("src",)

    def test_has_test_roots(self) -> None:
        assert _new_profile().test_roots == ("tests",)

    def test_has_framework_indicators(self) -> None:
        assert _new_profile().framework_indicators == ()

    def test_has_validation_commands(self) -> None:
        assert _new_profile().validation_commands == ()

    def test_has_ci_workflows(self) -> None:
        assert _new_profile().ci_workflows == ()

    def test_has_architecture_summary(self) -> None:
        assert _new_profile().architecture_summary == ""

    def test_has_conventions(self) -> None:
        assert _new_profile().conventions == ()

    def test_has_baseline_validation_status(self) -> None:
        assert _new_profile().baseline_validation_status == "unknown"


class TestRepositoryProfileStrict:
    """The profile must reject unknown keys and refuse mutation."""

    def test_unknown_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            RepositoryProfile(unknown_field="x")  # type: ignore[call-arg]

    def test_profile_is_frozen(self) -> None:
        p = _new_profile()
        with pytest.raises(ValidationError):
            p.name = "other"  # type: ignore[misc]


class TestRepositoryProfileBaselineStatus:
    """Baseline status is constrained to a small enum-like vocabulary."""

    def test_baseline_can_be_pass(self) -> None:
        assert _new_profile(baseline_validation_status="pass").baseline_validation_status == "pass"

    def test_baseline_can_be_fail(self) -> None:
        assert _new_profile(baseline_validation_status="fail").baseline_validation_status == "fail"

    def test_baseline_can_be_unknown(self) -> None:
        assert (
            _new_profile(baseline_validation_status="unknown").baseline_validation_status
            == "unknown"
        )

    def test_baseline_can_be_partial(self) -> None:
        assert (
            _new_profile(baseline_validation_status="partial").baseline_validation_status
            == "partial"
        )
