"""WP8 (story M) — OpenTelemetry-compatible trace export.

The handoff doc acceptance criteria for telemetry:

* Provide OpenTelemetry-compatible trace export.

This module ships a minimal OTLP-shaped JSON emitter that writes
spans to a configurable sink (file, stdout, or a structured log
callback). The orchestrator wraps every phase handler in a span
so the controller + dashboard can reconstruct the timeline.

We deliberately avoid pinning the OpenTelemetry SDK as a hard
dependency — most operators can ingest the JSON shape via
``otel-cli``, ``vector``, or any OTLP collector front-end. The
JSON schema is the OTLP/JSON Trace v1 wire format (subset we
actually emit), so a deployment that wants the SDK can replay
the file with ``opentelemetry-exporter-otlp-json``.

Wire format (one span per line):

    {
      "name": "_phase_implementation",
      "context": {
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef"
      },
      "parent_id": "...",   // optional
      "start_time_unix_nano": 1700000000000000000,
      "end_time_unix_nano": 1700000000123456789,
      "status": {"code": "OK"},      // or ERROR
      "attributes": {"phase": "...", "run_id": "..."},
      "events": [{"name": "...", "time_unix_nano": ..., "attributes": {}}]
    }
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO


def _utcnow_nano() -> int:
    return time.time_ns()


def _new_trace_id() -> str:
    return uuid.uuid4().hex


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


class _Status:
    OK = "OK"
    ERROR = "ERROR"
    UNSET = "UNSET"


@dataclass(frozen=True)
class SpanEvent:
    """OTLP-shaped event attached to a span."""

    name: str
    time_unix_nano: int
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass
class Span:
    """In-flight span being recorded by the tracer."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start_time_unix_nano: int
    end_time_unix_nano: int | None = None
    status: str = _Status.UNSET
    attributes: dict[str, object] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, code: str) -> None:
        self.status = code

    def add_event(
        self,
        name: str,
        *,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        self.events.append(
            SpanEvent(
                name=name,
                time_unix_nano=_utcnow_nano(),
                attributes=dict(attributes or {}),
            )
        )

    def to_jsonable(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "context": {
                "trace_id": self.trace_id,
                "span_id": self.span_id,
            },
            "parent_id": self.parent_id,
            "start_time_unix_nano": self.start_time_unix_nano,
            "end_time_unix_nano": self.end_time_unix_nano,
            "status": {"code": self.status},
            "attributes": dict(self.attributes),
            "events": [
                {
                    "name": ev.name,
                    "time_unix_nano": ev.time_unix_nano,
                    "attributes": dict(ev.attributes),
                }
                for ev in self.events
            ],
        }


class Tracer:
    """OTLP-compatible JSON tracer writing to a sink.

    The sink can be a path (``Path``/``str``), an existing file
    handle (``TextIO``), or a callable that accepts a dict. The
    default sink writes one span per line to stdout so operators
    can pipe to ``otel-cli`` or any JSON collector.
    """

    def __init__(
        self,
        sink: Path | str | TextIO | Callable[[Mapping[str, object]], None] | None = None,
        *,
        service_name: str = "seharness-orchestrator",
    ) -> None:
        self._sink_callable: Callable[[Mapping[str, object]], None]
        self._sink_path: Path | None = None
        self._sink_handle: TextIO | None = None
        self.service_name = service_name
        self._lock = threading.Lock()
        if sink is None:
            self._sink_callable = self._default_stdout_sink
        elif callable(sink):
            self._sink_callable = sink
        elif isinstance(sink, (Path, str)):
            self._sink_path = Path(sink)
            self._sink_path.parent.mkdir(parents=True, exist_ok=True)
            self._sink_callable = self._file_sink
        else:
            self._sink_handle = sink
            self._sink_callable = self._stream_sink

    # ----- Sinks -----

    def _default_stdout_sink(self, span: Mapping[str, object]) -> None:
        print(json.dumps(span), flush=True)

    def _file_sink(self, span: Mapping[str, object]) -> None:
        assert self._sink_path is not None
        with self._sink_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span) + "\n")

    def _stream_sink(self, span: Mapping[str, object]) -> None:
        assert self._sink_handle is not None
        self._sink_handle.write(json.dumps(span) + "\n")
        self._sink_handle.flush()

    # ----- Public API -----

    def start_span(
        self,
        name: str,
        *,
        parent: Span | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Span:
        return Span(
            name=name,
            trace_id=parent.trace_id if parent else _new_trace_id(),
            span_id=_new_span_id(),
            parent_id=parent.span_id if parent else None,
            start_time_unix_nano=_utcnow_nano(),
            attributes=dict(attributes or {}),
        )

    def end_span(
        self,
        span: Span,
        *,
        status: str = _Status.OK,
    ) -> None:
        span.end_time_unix_nano = _utcnow_nano()
        span.status = status
        # Inject the service.name attribute (OTel semantic convention).
        span.attributes.setdefault("service.name", self.service_name)
        payload = span.to_jsonable()
        with self._lock:
            self._sink_callable(payload)

    @contextmanager
    def span(
        self,
        name: str,
        *,
        parent: Span | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        active = self.start_span(name, parent=parent, attributes=attributes)
        status = _Status.OK
        try:
            yield active
        except BaseException as exc:
            status = _Status.ERROR
            active.set_attribute("exception.type", type(exc).__name__)
            active.set_attribute("exception.message", str(exc))
            raise
        finally:
            self.end_span(active, status=status)

    def close(self) -> None:
        if self._sink_handle is not None:
            with suppress(OSError):
                self._sink_handle.close()


class NullTracer:
    """No-op tracer used when telemetry is disabled.

    Drop-in replacement for :class:`Tracer` so callers can swap
    implementations based on ``OrchestratorConfig.trace_sink``.
    """

    @contextmanager
    def span(
        self,
        name: str,
        *,
        parent: Span | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[Span]:
        active = Span(
            name=name,
            trace_id="",
            span_id="",
            parent_id=None,
            start_time_unix_nano=0,
        )
        yield active

    def start_span(self, *args: object, **kwargs: object) -> Span:
        return Span(
            name=str(args[0]) if args else "",
            trace_id="",
            span_id="",
            parent_id=None,
            start_time_unix_nano=0,
        )

    def end_span(self, *args: object, **kwargs: object) -> None:
        return None

    def close(self) -> None:
        return None


def build_tracer_from_env() -> Tracer | NullTracer:
    """Construct a tracer from the ``SEHARNESS_TRACE_SINK`` env var.

    Values:
      * ``"off"`` / ``"null"`` → :class:`NullTracer` (default).
      * ``"stdout"`` → OTLP JSON lines on stdout.
      * any path → append-mode JSON lines file.
    """
    raw = os.environ.get("SEHARNESS_TRACE_SINK", "off").strip().lower()
    if raw in {"", "off", "null", "none", "no"}:
        return NullTracer()
    if raw == "stdout":
        return Tracer()
    return Tracer(sink=raw)


__all__ = [
    "NullTracer",
    "Span",
    "SpanEvent",
    "Tracer",
    "build_tracer_from_env",
]


# Sentinel to keep the unused imports honest for callers introspecting the
# module's surface.
_ = (Iterable,)
