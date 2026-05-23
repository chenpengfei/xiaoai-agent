from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SpanHandle:
    name: str
    trace_id: str
    span_id: str
    _span: Any = None
    _context: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is not None and value is not None:
            self._span.set_attribute(key, value)

    def set_error(self, error: BaseException | str) -> None:
        if self._span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            if isinstance(error, BaseException):
                self._span.record_exception(error)
                message = str(error)
            else:
                message = error
            self._span.set_status(Status(StatusCode.ERROR, message))
        except Exception:
            return

    def end(self) -> None:
        if self._span is not None:
            self._span.end()


class TraceManager:
    def __init__(self, *, enabled: bool, tracer: Any = None) -> None:
        self.enabled = enabled
        self._tracer = tracer

    @classmethod
    def from_env(cls) -> "TraceManager":
        enabled = os.getenv("VOICE_GATEWAY_OTEL_ENABLED", "0") not in {"", "0", "false", "False"}
        if not enabled:
            return cls(enabled=False)
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception:
            return cls(enabled=False)

        endpoint = os.getenv("VOICE_GATEWAY_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "voice-gateway",
                    "deployment.environment": os.getenv("VOICE_GATEWAY_ENV", "home"),
                }
            )
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        return cls(enabled=True, tracer=trace.get_tracer("voice-gateway"))

    def start_root_span(self, name: str, attributes: Optional[dict[str, Any]] = None) -> SpanHandle:
        if self._tracer is None:
            return _new_noop_span(name)
        span = self._tracer.start_span(name, attributes=_clean_attributes(attributes or {}))
        return _handle_for_span(name, span)

    def start_child_span(
        self,
        name: str,
        parent: Optional[SpanHandle],
        attributes: Optional[dict[str, Any]] = None,
    ) -> SpanHandle:
        if self._tracer is None:
            return SpanHandle(name=name, trace_id=parent.trace_id if parent else uuid.uuid4().hex, span_id=_new_span_id())
        context = parent._context if parent is not None else None
        span = self._tracer.start_span(name, context=context, attributes=_clean_attributes(attributes or {}))
        return _handle_for_span(name, span)


def _handle_for_span(name: str, span: Any) -> SpanHandle:
    try:
        from opentelemetry import trace

        context = span.get_span_context()
        return SpanHandle(
            name=name,
            trace_id=f"{context.trace_id:032x}",
            span_id=f"{context.span_id:016x}",
            _span=span,
            _context=trace.set_span_in_context(span),
        )
    except Exception:
        return _new_noop_span(name)


def _new_noop_span(name: str) -> SpanHandle:
    return SpanHandle(name=name, trace_id=uuid.uuid4().hex, span_id=_new_span_id())


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _clean_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            clean[key] = value
    return clean
