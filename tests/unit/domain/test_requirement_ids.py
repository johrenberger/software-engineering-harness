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
from pydantic import BaseModel, ValidationError

from seharness.domain.requirements import (
    FunctionalRequirementId,
    NonFunctionalRequirementId,
    RequirementKind,
    ScenarioId,
    requirement_kind,
)


class _FrHolder(BaseModel):
    model_config = {"extra": "forbid"}
    rid: FunctionalRequirementId


class _NfrHolder(BaseModel):
    model_config = {"extra": "forbid"}
    rid: NonFunctionalRequirementId


class _ScnHolder(BaseModel):
    model_config = {"extra": "forbid"}
    sid: ScenarioId


class TestFunctionalRequirementIdFormat:
    def test_accepts_fr_prefix(self) -> None:
        assert _FrHolder(rid="FR-1").rid == "FR-1"

    def test_accepts_fr_multi_digit(self) -> None:
        assert _FrHolder(rid="FR-42").rid == "FR-42"

    def test_accepts_fr_three_digit(self) -> None:
        assert _FrHolder(rid="FR-123").rid == "FR-123"

    def test_rejects_nfr_prefix(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="NFR-1")

    def test_rejects_scn_prefix(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="SCN-1")

    def test_rejects_lowercase(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="fr-1")

    def test_rejects_missing_number(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="FR-")

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="FR-0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            _FrHolder(rid="FR--1")


class TestNonFunctionalRequirementIdFormat:
    def test_accepts_nfr_prefix(self) -> None:
        assert _NfrHolder(rid="NFR-1").rid == "NFR-1"

    def test_rejects_fr_prefix(self) -> None:
        with pytest.raises(ValidationError):
            _NfrHolder(rid="FR-1")


class TestScenarioIdFormat:
    def test_accepts_scn_prefix(self) -> None:
        assert _ScnHolder(sid="SCN-1").sid == "SCN-1"

    def test_rejects_fr_prefix(self) -> None:
        with pytest.raises(ValidationError):
            _ScnHolder(sid="FR-1")


class TestRequirementKind:
    def test_fr_kind_value(self) -> None:
        assert RequirementKind.FUNCTIONAL.value == "functional"

    def test_nfr_kind_value(self) -> None:
        assert RequirementKind.NON_FUNCTIONAL.value == "non_functional"

    def test_scenario_kind_value(self) -> None:
        assert RequirementKind.SCENARIO.value == "scenario"

    def test_requirement_kind_helper_for_fr(self) -> None:
        assert requirement_kind("FR-1") == RequirementKind.FUNCTIONAL

    def test_requirement_kind_helper_for_nfr(self) -> None:
        assert requirement_kind("NFR-1") == RequirementKind.NON_FUNCTIONAL

    def test_requirement_kind_helper_for_scn(self) -> None:
        assert requirement_kind("SCN-1") == RequirementKind.SCENARIO

    def test_requirement_kind_helper_rejects_unknown(self) -> None:
        with pytest.raises(ValueError):
            requirement_kind("XX-1")
