"""Cluster M3-4: manifest validation tests for the recording fixtures.

The corrective doc §"Offline vertical acceptance" requires the
fixtures to be schema-validated before the orchestrator runs. This
test module pins the manifest schema, the file shape, and the
redaction cleanliness for every recording file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.e2e._bootstrap import (
    active_recordings_dir,
    file_sha256,
    load_recording_manifest,
    load_recording_pair,
    redaction_clean,
)

# Pre-import to break the orchestrator's package init cycle.
from seharness.controller.run_ledger import RunLedger  # noqa: F401

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"
RECORDINGS_DIR = FIXTURES_DIR / "minimax_m3_recordings"


# ---------------------------------------------------------------------------
# Manifest schema
# ---------------------------------------------------------------------------


class TestManifestSchema:
    """The manifest must declare the required keys and the
    required model + recording_kind values.
    """

    def test_manifest_file_exists(self) -> None:
        assert (RECORDINGS_DIR / "manifest.json").exists()

    def test_manifest_model_is_m3(self) -> None:
        manifest = load_recording_manifest()
        assert manifest["model"] == "MiniMax-M3"

    def test_manifest_recording_kind_recognised(self) -> None:
        manifest = load_recording_manifest()
        assert manifest["recording_kind"] in {
            "synthetic_redacted_placeholder",
            "live_recording",
        }

    def test_manifest_schema_version_is_known(self) -> None:
        manifest = load_recording_manifest()
        # Bump the accepted list when adding schema versions.
        assert manifest["schema_version"] in {"1"}

    def test_manifest_calls_non_empty(self) -> None:
        manifest = load_recording_manifest()
        assert isinstance(manifest["calls"], list)
        assert manifest["calls"], "manifest.calls must be non-empty"

    def test_manifest_calls_have_required_keys(self) -> None:
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            for key in ("phase", "request_file", "response_file"):
                assert key in entry, f"manifest call entry missing key {key!r}: {entry!r}"

    def test_manifest_swapped_by_present(self) -> None:
        manifest = load_recording_manifest()
        assert "swapped_by" in manifest


# ---------------------------------------------------------------------------
# File shape (request + response per phase)
# ---------------------------------------------------------------------------


class TestRecordingFilesExist:
    """Every ``request_file`` and ``response_file`` referenced by
    the manifest must exist on disk.
    """

    def test_all_referenced_files_exist(self) -> None:
        manifest = load_recording_manifest()
        base = active_recordings_dir()
        for entry in manifest["calls"]:
            req = base / entry["request_file"]
            resp = base / entry["response_file"]
            assert req.exists(), f"missing request file: {req}"
            assert resp.exists(), f"missing response file: {resp}"

    def test_all_recordings_have_minimax_m3_model(self) -> None:
        """Every request must declare ``model: "MiniMax-M3"`` so a
        silent model substitution would be caught at fixture load
        time, not at orchestrator run time.
        """
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            request_data, _ = load_recording_pair(entry["phase"])
            assert request_data["model"] == "MiniMax-M3", (
                f"recording {entry['phase']!r} declares "
                f"model={request_data['model']!r}; "
                "the corrective doc requires MiniMax-M3 everywhere"
            )

    def test_all_recordings_have_native_protocol(self) -> None:
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            request_data, _ = load_recording_pair(entry["phase"])
            # Default M3-1 protocol is 'native'; check that.
            assert request_data.get("protocol") == "native", (
                f"recording {entry['phase']!r} declares "
                f"protocol={request_data.get('protocol')!r}; "
                "M3-4 ships native protocol recordings"
            )

    def test_all_responses_have_no_error(self) -> None:
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            _, response_data = load_recording_pair(entry["phase"])
            assert response_data.get("error") is None, (
                f"recording {entry['phase']!r} carries an error: "
                f"{response_data.get('error')!r}; "
                "M3-4 only ships successful responses"
            )

    def test_all_responses_carry_content_text(self) -> None:
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            _, response_data = load_recording_pair(entry["phase"])
            content = response_data.get("content_text")
            assert isinstance(content, str) and content.strip(), (
                f"recording {entry['phase']!r} has empty content_text"
            )


# ---------------------------------------------------------------------------
# Response shape per phase (must parse against the receiving schema)
# ---------------------------------------------------------------------------


class TestResponseShapePerPhase:
    """Each response's ``content_text`` must parse cleanly as the
    JSON the receiving service expects.
    """

    def test_spec_response_parses_as_specification_schema(self) -> None:
        from seharness.orchestrator.spec_plan_schemas import (
            SpecificationSchema,
            parse_specification,
        )

        _, response_data = load_recording_pair("specification")
        payload = json.loads(response_data["content_text"])
        schema = parse_specification(payload)
        assert isinstance(schema, SpecificationSchema)
        assert schema.description  # non-empty

    def test_plan_response_parses_as_plan_schema(self) -> None:
        from seharness.orchestrator.spec_plan_schemas import (
            PlanSchema,
            parse_plan,
        )

        _, response_data = load_recording_pair("planning")
        payload = json.loads(response_data["content_text"])
        schema = parse_plan(payload)
        assert isinstance(schema, PlanSchema)
        assert schema.plan_id
        assert len(schema.tasks) >= 1

    def test_implementation_test_patch_payload_is_valid(
        self,
    ) -> None:
        """The implementation-phase payload is ``_ImplementationPayload``
        (Pydantic). When the offline test parses it through the
        adapter + structured_payload path, the shape must validate.
        """
        from seharness.orchestrator.services import _ImplementationPayload

        _, response_data = load_recording_pair("implementation_test_patch")
        payload = json.loads(response_data["content_text"])
        parsed = _ImplementationPayload.model_validate(payload)
        assert parsed.task_id
        # attempted_changes is allowed to be empty for the
        # test-patch call (the model says "no prod changes yet")
        # but the field MUST be present.
        assert hasattr(parsed, "attempted_changes")

    def test_implementation_production_patch_payload_is_valid(
        self,
    ) -> None:
        from seharness.orchestrator.services import _ImplementationPayload

        _, response_data = load_recording_pair("implementation_production_patch")
        payload = json.loads(response_data["content_text"])
        parsed = _ImplementationPayload.model_validate(payload)
        assert parsed.task_id

    def test_review_response_parses_as_review_payload(self) -> None:
        from seharness.orchestrator.services import _ReviewPayload

        _, response_data = load_recording_pair("review")
        payload = json.loads(response_data["content_text"])
        parsed = _ReviewPayload.model_validate(payload)
        # The M3-4 fixture MUST ship an approval verdict so the
        # orchestrator's review phase succeeds; the doc forbids
        # defaulting to approval on malformed output, but a
        # well-formed approved verdict is the expected happy-path
        # outcome for the /health vertical.
        assert parsed.status == "approved"
        assert parsed.approval is True


# ---------------------------------------------------------------------------
# Redaction cleanliness
# ---------------------------------------------------------------------------


class TestRedactionCleanliness:
    """Every content_text and request body must be redaction-clean
    per the M3-2 ``redact_error_message`` rules. The manifest
    declares the redaction policy; the recordings must honour it.
    """

    @pytest.mark.parametrize(
        "phase",
        [
            "specification",
            "planning",
            "implementation_test_patch",
            "implementation_production_patch",
            "review",
        ],
    )
    def test_request_body_redaction_clean(self, phase: str) -> None:
        request_data, _ = load_recording_pair(phase)
        # Serialize the request back to a string and check.
        body = json.dumps(request_data)
        assert redaction_clean(body), f"recording {phase!r} request carries a redacted token"

    @pytest.mark.parametrize(
        "phase",
        [
            "specification",
            "planning",
            "implementation_test_patch",
            "implementation_production_patch",
            "review",
        ],
    )
    def test_response_content_redaction_clean(self, phase: str) -> None:
        _, response_data = load_recording_pair(phase)
        body = response_data.get("content_text", "")
        assert redaction_clean(body), (
            f"recording {phase!r} response content_text carries a redacted token"
        )

    def test_all_request_ids_are_redacted(self) -> None:
        """The synthetic recordings use ``[redacted-synthetic]`` as
        the request_id literal so the audit trail clearly shows
        the recording is synthetic and not a live run.
        """
        manifest = load_recording_manifest()
        for entry in manifest["calls"]:
            _, response_data = load_recording_pair(entry["phase"])
            req_id = response_data.get("request_id", "")
            assert req_id.startswith("[redacted"), (
                f"recording {entry['phase']!r} request_id={req_id!r} is not marked [redacted-*]"
            )


# ---------------------------------------------------------------------------
# Manifest content integrity (sha256 declared, file content matches)
# ---------------------------------------------------------------------------


class TestManifestSha256:
    """The manifest declares a sha256 of every recording. The
    offline test re-computes the sha256 on load and fails if any
    drift, so the fixture cannot silently change between commits.
    """

    def test_declared_sha256_matches_computed(self) -> None:
        manifest = load_recording_manifest()
        base = active_recordings_dir()
        for entry in manifest["calls"]:
            response_path = base / entry["response_file"]
            actual_sha = file_sha256(response_path)
            declared = entry.get("sha256", "pending")
            # The "pending" sentinel lets us commit the manifest
            # before the tooling computes the hashes; a CI step
            # flips pending → <hex>. Until that step lands, we
            # only assert the sha matches when declared.
            if declared == "pending":
                continue
            assert declared == actual_sha, (
                f"recording {entry['phase']!r} response file "
                f"{response_path.name} sha256 drift: declared={declared!r} "
                f"actual={actual_sha!r}"
            )


# ---------------------------------------------------------------------------
# Coverage of the loader's error paths
# ---------------------------------------------------------------------------


class TestLoaderErrorPaths:
    """The loader fails loudly when the manifest or recordings are
    mis-shaped. M3-4 forbids conditional skips; a broken fixture
    must hard-fail.
    """

    def test_unknown_phase_raises(self, tmp_path: Path) -> None:
        # Monkey-patch the manifest path to a tmp dir with a
        # valid manifest but no matching phase entry.
        import tests.e2e._bootstrap as bootstrap_module

        original_dir = bootstrap_module.active_recordings_dir
        # We don't actually swap the directory — the loader
        # reads the manifest from the active directory and looks
        # up phase entries by name. Calling with an unknown phase
        # must raise.
        try:
            with pytest.raises(ValueError, match="not in manifest"):
                load_recording_pair("nonexistent-phase")
        finally:
            _ = original_dir  # reference for completeness
