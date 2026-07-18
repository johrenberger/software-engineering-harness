"""Tests for SPEC §'Slice 11: Telegram ingress' RED bullet 2.

'/feature invokes the same application service as CLI':
- Telegram /feature handler MUST route to ApplicationService.feature_request().
- The request MUST be normalized to the same FeatureRequest contract
  as the CLI's `seharness run --repository <url> --feature <desc>`.
- Telegram handlers MUST NOT contain workflow logic (parse + dispatch
  only).
- /feature with missing repository URL returns bounded error message
  (interactive prompt or inline error — bounded result).
"""

from __future__ import annotations

import pytest

from seharness.telegram.commands import CommandKind, ParsedCommand
from seharness.telegram.handlers import FeatureHandler, StubApplicationService
from seharness.telegram.service import ApplicationService, FeatureRequest


def _parsed(*, args: tuple[str, ...] = ()) -> ParsedCommand:
    return ParsedCommand(
        kind=CommandKind.FEATURE,
        chat_id=12345,
        args=args,
        raw_text="/feature",
    )


def test_application_service_protocol_exposes_feature_request() -> None:
    """ApplicationService Protocol has feature_request() (slice-12 wires the real impl)."""
    assert callable(getattr(ApplicationService, "feature_request", None))


def test_feature_request_is_dataclass() -> None:
    """FeatureRequest is a frozen Pydantic BaseModel."""
    from pydantic import BaseModel

    req = FeatureRequest(repository_url="https://github.com/foo/bar", description="Add X")
    assert isinstance(req, BaseModel)


def test_feature_handler_calls_application_service() -> None:
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    result = handler.handle(
        _parsed(args=("https://github.com/foo/bar", "Add login screen"))
    )
    assert result.ok is True
    assert len(app.calls) == 1
    req = app.calls[0]
    assert req.repository_url == "https://github.com/foo/bar"
    assert req.description == "Add login screen"


def test_feature_handler_passes_repo_and_description_distinct() -> None:
    """Multi-word description MUST be preserved (no shell-style split)."""
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    handler.handle(
        _parsed(args=("https://github.com/foo/bar", "Add", "login", "screen"))
    )
    assert app.calls[0].description == "Add login screen"


def test_feature_handler_returns_bounded_result() -> None:
    """Result has ok + message + bounded size."""
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    result = handler.handle(
        _parsed(args=("https://github.com/foo/bar", "X"))
    )
    assert hasattr(result, "ok")
    assert hasattr(result, "message")
    assert len(result.message) <= 4096  # Telegram message cap


def test_feature_handler_returns_error_for_missing_args() -> None:
    """No args → error result (interactive prompt or inline error)."""
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    result = handler.handle(_parsed(args=()))
    assert result.ok is False
    assert "repository" in result.message.lower() or "url" in result.message.lower()


def test_feature_handler_does_not_start_run_for_invalid_input() -> None:
    """Malformed input MUST NOT call application service."""
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    handler.handle(_parsed(args=()))
    assert app.calls == ()


def test_feature_request_model_rejects_empty_repository() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FeatureRequest(repository_url="", description="x")


def test_feature_request_model_rejects_empty_description() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FeatureRequest(repository_url="https://example.com", description="")


def test_stub_application_service_records_all_calls() -> None:
    """StubApplicationService records FeatureRequest sequence (idempotency test)."""
    app = StubApplicationService()
    handler = FeatureHandler(application=app)
    handler.handle(_parsed(args=("u1", "d1")))
    handler.handle(_parsed(args=("u2", "d2")))
    assert len(app.calls) == 2
    assert [r.repository_url for r in app.calls] == ["u1", "u2"]