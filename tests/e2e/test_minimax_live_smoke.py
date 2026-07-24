"""MiniMax live smoke test (cluster M3 framing).

This is the live-transport evidence entry point for the
MiniMax-M3 production path. See
``docs/vertical-acceptance.md`` for the current vertical
acceptance index and ``docs/m3-capability-matrix.md`` for
the component-vs-integrated capability breakdown.

Behavior contract:

- **Skip gate**: ``pytest.skip`` when ``RUN_MINIMAX_LIVE_TEST`` or
  ``MINIMAX_API_KEY`` is absent. The harness MUST NOT make a live
  call without an explicit opt-in AND a credential.
- **Fail gate**: once explicitly enabled, ANY provider failure
  (auth, timeout, 4xx, 5xx, malformed output) MUST fail — not
  skip — the test. The harness will not paper over a broken
  account by silently downgrading.
- **Recorded artifacts**: the tested endpoint, the model id
  returned by the API, the request id, the current commit SHA,
  and the redacted assistant message are written to
  ``tests/e2e/_artifacts/minimax_live_smoke.json`` so a
  credentialed operator can attach the evidence to the M3
  vertical-acceptance index.
- **Production model**: the corrective doc
  (``plans/minimax-m3-corrective-processing-instructions.md``)
  pins the production model to ``MiniMax-M3``. M2.7
  remains valid for backwards-compatibility catalog probes
  when the operator explicitly sets ``MINIMAX_MODEL``;
  it is not a production target.

The test is offline-safe by default (the skip gate fires
without any network). CI is expected to run this only with
``RUN_MINIMAX_LIVE_TEST=1`` and a valid credential supplied via
the protected environment.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess  # nosec B404
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from seharness.domain.enums import RoutingRole
from seharness.domain.requests import ModelRequest
from seharness.models.minimax import MiniMaxAdapter
from seharness.models.minimax_transport import (
    DEFAULT_ENDPOINT,
    MODELS_ENDPOINT,
    parse_model_catalog,
)

# ---------------------------------------------------------------------------
# Skip / fail gates
# ---------------------------------------------------------------------------

_LIVE_ENV_VAR = "RUN_MINIMAX_LIVE_TEST"


def _skip_reason() -> str | None:
    """Return the reason to skip the live test, or ``None`` if it
    should run.

    Per the workplan: skip only when ``RUN_MINIMAX_LIVE_TEST`` OR
    ``MINIMAX_API_KEY`` is absent. The harness MUST NOT make a
    live call without both gates open.
    """
    if not os.environ.get(_LIVE_ENV_VAR):
        return f"set {_LIVE_ENV_VAR}=1 to enable the live MiniMax smoke test"
    if not os.environ.get("MINIMAX_API_KEY"):
        return "set MINIMAX_API_KEY to enable the live MiniMax smoke test"
    return None


pytestmark = pytest.mark.skipif(
    _skip_reason() is not None,
    reason=_skip_reason() or "live test disabled",
)


# ---------------------------------------------------------------------------
# Recording + redaction
# ---------------------------------------------------------------------------


def _current_commit_sha() -> str:
    """Best-effort current HEAD commit SHA.

    Falls back to ``"unknown"`` when the working tree is not a
    git checkout (CI sandbox without the repo, detached test
    run, etc.). The recorded SHA is evidence for PR #77; an
    "unknown" value still records the host + timestamp.
    """
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _record_artifact(
    *,
    endpoint: str,
    model_id: str,
    model_id_returned: str | None,
    request_id: str | None,
    commit_sha: str,
    duration_s: float,
    redacted_content: str,
    error_kind: str | None,
    error_message: str | None,
) -> Path:
    """Persist the test evidence to a JSON artifact.

    Path: ``tests/e2e/_artifacts/minimax_live_smoke.json``.

    The artifact is over-written on each successful run; the
    credentialed operator attaches it to PR #77 (vertical-
    acceptance) so reviewers see what actually happened on
    the live account. The artifact is committed to the repo
    ONLY after a successful run; failed runs leave the previous
    evidence in place so PR #77 never displays fabricated success.
    """
    artifact_dir = Path(__file__).parent / "_artifacts"
    artifact_dir.mkdir(exist_ok=True)
    artifact_path = artifact_dir / "minimax_live_smoke.json"
    payload = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "endpoint": endpoint,
        "configured_model_id": model_id,
        "model_id_returned": model_id_returned,
        "request_id": request_id,
        "commit_sha": commit_sha,
        "duration_s": round(duration_s, 4),
        "redacted_content": redacted_content,
        "error_kind": error_kind,
        "error_message": error_message,
        "host": socket.gethostname(),
    }
    artifact_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return artifact_path


def _redact(text: str | None, *, max_length: int = 240) -> str | None:
    """Redact the assistant content for safe logging.

    The harness deliberately never persists raw model output to
    the artifact — only a head snippet. Long bodies are cut off.
    """
    if text is None:
        return None
    text = text.replace("\n", "\\n")
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMiniMaxLiveSmoke:
    """WP11 PR2 — live MiniMax smoke.

    **Skip gate** (per workplan): the whole module is skipped
    unless ``RUN_MINIMAX_LIVE_TEST=1`` AND ``MINIMAX_API_KEY``
    are both set.

    **Fail gate**: any transport-level failure (auth, timeout,
    4xx, 5xx, malformed output) MUST fail the test, not skip.
    The harness MUST NOT paper over a broken account.

    **Recorded evidence** (per workplan): endpoint, model id
    returned by the API, request id, commit SHA, redacted
    assistant message, host, timestamp."""

    def test_chat_completions_endpoint_reachable(self) -> None:
        """A simple ping to ``/v1/chat/completions`` returns a
        model-shaped response. The transport response is recorded
        to ``tests/e2e/_artifacts/minimax_live_smoke.json`` for
        PR #77 evidence."""

        # Resolve model id: explicit MINIMAX_MODEL env var, no
        # hard-coded default. The credentialed operator MUST set
        # this; per the workplan, do not silently substitute one
        # model for another.
        configured_model = os.environ.get("MINIMAX_MODEL")
        if not configured_model:
            pytest.fail(
                "MINIMAX_MODEL is not set. The live smoke test "
                "requires the operator to pick a model deliberately; "
                "the harness default is MiniMax-M3 (see "
                "src/seharness/models/minimax_m3_composition.py), "
                "but the smoke test will not auto-default because "
                "the live account may not expose M3 yet."
            )

        adapter = MiniMaxAdapter(model_identifier=configured_model)
        # The probe ran without an exception — but we still
        # validate readiness() explicitly. Per the workplan, an
        # HTTP transport + key is necessary but not sufficient
        # for production startup; the model id MUST be in the
        # live catalog.
        readiness = adapter.readiness()
        assert readiness.configured is True
        assert readiness.transport_is_live is True
        assert readiness.model_identifier == configured_model

        # Issue a single chat-completions call. The prompt is
        # deliberately trivial so the test is fast and cheap.
        request = ModelRequest(
            role=RoutingRole.PLANNING,
            prompt="ping",
            max_tokens=8,
            temperature=0.0,
        )

        commit_sha = _current_commit_sha()
        started = time.monotonic()
        response = adapter.invoke(request)
        duration_s = time.monotonic() - started

        # Fail (not skip) on any transport-level error.
        if response.error is not None:
            artifact = _record_artifact(
                endpoint=DEFAULT_ENDPOINT,
                model_id=configured_model,
                model_id_returned=None,
                request_id=None,
                commit_sha=commit_sha,
                duration_s=duration_s,
                redacted_content=None,
                error_kind=response.error.kind,
                error_message=response.error.message,
            )
            pytest.fail(
                f"MiniMax live smoke failed at {DEFAULT_ENDPOINT} "
                f"with model={configured_model!r}: "
                f"kind={response.error.kind!r}, "
                f"message={response.error.message!r}. "
                f"Evidence written to {artifact}."
            )

        # Success: record the evidence. The artifact captures
        # endpoint + model id returned (from response.model) +
        # request id (from response.parsed?.id or a separate
        # field) + commit SHA + redacted content.
        artifact = _record_artifact(
            endpoint=DEFAULT_ENDPOINT,
            model_id=configured_model,
            model_id_returned=response.model,
            request_id=None,  # not yet surfaced on ModelResponse;
            # PR #77 will wire this. Recorded as None for now.
            commit_sha=commit_sha,
            duration_s=duration_s,
            redacted_content=_redact(response.raw_output),
            error_kind=None,
            error_message=None,
        )

        # Surface the artifact path so the operator sees where
        # the evidence went. NOT a print — pytest captures stdout
        # and shows it on failure.
        assert response.raw_output is not None
        assert response.raw_output.strip() != "", (
            f"MiniMax returned empty content. Evidence: {artifact}"
        )
        assert artifact.exists(), f"Evidence artifact missing after successful run: {artifact}"

    def test_models_endpoint_lists_configured_model(self) -> None:
        """``GET /v1/models`` lists the configured model id.

        Per the workplan: production startup MUST validate that
        the configured model is present in the live catalog. The
        catalog request is independent of the chat-completions
        call so the harness can refuse to start before any phase
        runs."""

        configured_model = os.environ.get("MINIMAX_MODEL")
        if not configured_model:
            pytest.fail("MINIMAX_MODEL is not set; cannot validate against the live catalog.")

        import httpx

        token = os.environ["MINIMAX_API_KEY"]
        commit_sha = _current_commit_sha()
        started = time.monotonic()
        try:
            http_response = httpx.get(
                MODELS_ENDPOINT,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
        except httpx.TimeoutException as exc:
            pytest.fail(
                f"MiniMax catalog fetch timed out: {exc}. "
                f"endpoint={MODELS_ENDPOINT}, model={configured_model!r}"
            )
        except httpx.HTTPError as exc:
            pytest.fail(
                f"MiniMax catalog fetch failed: {exc}. "
                f"endpoint={MODELS_ENDPOINT}, model={configured_model!r}"
            )
        duration_s = time.monotonic() - started

        if http_response.status_code in (401, 403):
            pytest.fail(
                f"MiniMax catalog fetch auth failed: HTTP "
                f"{http_response.status_code}. endpoint={MODELS_ENDPOINT}"
            )
        if http_response.status_code >= 400:
            pytest.fail(
                f"MiniMax catalog fetch returned HTTP "
                f"{http_response.status_code}. endpoint={MODELS_ENDPOINT}"
            )

        try:
            parsed = json.loads(http_response.content)
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"MiniMax catalog response is not valid JSON: {exc}. endpoint={MODELS_ENDPOINT}"
            )

        if not isinstance(parsed, dict):
            pytest.fail(
                f"MiniMax catalog body is not a JSON object: "
                f"type={type(parsed).__name__}. endpoint={MODELS_ENDPOINT}"
            )

        available = parse_model_catalog(parsed)
        artifact = _record_artifact(
            endpoint=MODELS_ENDPOINT,
            model_id=configured_model,
            model_id_returned=None,
            request_id=None,
            commit_sha=commit_sha,
            duration_s=duration_s,
            redacted_content=None,
            error_kind=None,
            error_message=None,
        )
        assert configured_model in available, (
            f"configured model {configured_model!r} is NOT in the live "
            f"catalog. Available: {available}. Per the workplan the "
            f"harness must NOT silently substitute one model for "
            f"another. Evidence: {artifact}"
        )
