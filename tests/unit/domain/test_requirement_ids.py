"""RED — Slice 5 behavior 01: requirements receive stable IDs.

Per SPEC §15 ("Specification") and §28 (slice 5 RED bullets):

    requirements receive stable IDs

The harness uses prefixed stable IDs:
- FR-*  — functional requirements
- NFR-* — non-functional requirements
- SCN-* — BDD scenarios

Each ID must parse, round-trip, and reject malformed variants. IDs are part
of the harness contract (persisted in run-state.json and referenced by
plans / traceability).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from seharness.domain.requirements import (
    FunctionalRequirementId,
    NonFunctionalRequirementId,
    ScenarioId,
    RequirementKind,
)


class TestFunctionalRequirementIdFormat:
    def test_accepts_fr_prefix(self) -> None:
        rid = FunctionalRequirementId("FR-1")
        assert str(rid) == "FR-1"

    def test_accepts_fr_multi_digit(self) -> None:
        rid = FunctionalRequirementId("FR-42")
        assert str(rid) == "FR-42"

    def test_accepts_fr_three_digit(self) -> None:
        rid = FunctionalRequirementId("FR-123")
        assert str(rid) == "FR-123"

    def test_rejects_nfr_prefix(self) -> None:
        """Functional IDs must not accept NFR prefix."""
        with pytest.raises(ValidationError):
            FunctionalRequirementId("NFR-1")

    def test_rejects_scn_prefix(self) -> None:
        with pytest.raises(ValidationError):
            FunctionalRequirementId("SCN-1")

    def test_rejects_lowercase(self) -> None:
        with pytest.raises(ValidationError):
            FunctionalRequirementId("fr-1")

    def test_rejects_missing_number(self) -> None:
        with pytest.raises(ValidationError):
            FunctionalRequirementId("FR-")

    def test_rejects_zero(self) -> None:
        """Stable IDs are 1-based — zero is reserved for sentinel use."""
        with pytest.raises(ValidationError):
            FunctionalRequirementId("FR-0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            FunctionalRequirementId("FR--1")


class TestNonFunctionalRequirementIdFormat:
    def test_accepts_nfr_prefix(self) -> None:
        rid = NonFunctionalRequirementId("NFR-1")
        assert str(rid) == "NFR-1"

    def test_rejects_fr_prefix(self) -> None:
        with pytest.raises(ValidationError):
            NonFunctionalRequirementId("FR-1")


class TestScenarioIdFormat:
    def test_accepts_scn_prefix(self) -> None:
        sid = ScenarioId("SCN-1")
        assert str(sid) == "SCN-1"

    def test_rejects_fr_prefix(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioId("FR-1")


class TestRequirementKind:
    def test_fr_kind_value(self) -> None:
        assert RequirementKind.FUNCTIONAL.value == "functional"

    def test_nfr_kind_value(self) -> None:
        assert RequirementKind.NON_FUNCTIONAL.value == "non_functional"

    def test_scenario_kind_value(self) -> None:
        assert RequirementKind.SCENARIO.value == "scenario"