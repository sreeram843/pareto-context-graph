"""Lightweight request tracing with optional OTLP export (Phase 7.6, 14.1)."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from .metrics import METRICS

_lock = threading.Lock()
_spans: list[dict] = []
_MAX_SPANS = 512

_tls = threading.local()
_provider_initialized = False
_tracer: Any | None = None


def _otel_endpoint() -> str | None:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("PCG_OTEL_ENDPOINT")


def otel_enabled() -> bool:
    return bool(_otel_endpoint())


def _otel_protocol() -> str:
    raw = (
        os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or os.environ.get("PCG_OTEL_PROTOCOL")
        or "http/protobuf"
    )
    return raw.strip().lower()


def _service_name() -> str:
    return os.environ.get("OTEL_SERVICE_NAME", "pareto-context-graph")


def reset_tracing_for_test() -> None:
    """Reset module globals (tests only)."""
    global _provider_initialized, _tracer
    _provider_initialized = False
    _tracer = None
    _tls.root_span = None
    _tls.phase_span = None
    with _lock:
        _spans.clear()


def configure_tracer_provider(provider: Any) -> None:
    """Inject a TracerProvider (tests only)."""
    global _provider_initialized, _tracer
    from opentelemetry import trace

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("pareto_context_graph", "0.1.0")
    _provider_initialized = True


def _init_otel_tracer() -> Any | None:
    global _provider_initialized, _tracer
    if _provider_initialized:
        return _tracer
    _provider_initialized = True
    if not _otel_endpoint():
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return None

    endpoint = _otel_endpoint() or ""
    protocol = _otel_protocol()
    exporter: Any
    try:
        if protocol in {"grpc", "grpc/protobuf"}:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcExporter,
            )

            exporter = GrpcExporter(endpoint=endpoint)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpExporter,
            )

            exporter = HttpExporter(endpoint=endpoint)
    except ImportError:
        return None

    resource = Resource.create({"service.name": _service_name()})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("pareto_context_graph", "0.1.0")
    return _tracer


def _record_inproc_span(
    *,
    trace_id: str,
    span_id: str,
    name: str,
    duration_ms: float,
    attributes: dict[str, str],
) -> None:
    with _lock:
        _spans.append(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "name": name,
                "duration_ms": round(duration_ms, 3),
                **attributes,
            }
        )
        if len(_spans) > _MAX_SPANS:
            del _spans[: len(_spans) - _MAX_SPANS]


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    name: str
    start: float = field(default_factory=time.perf_counter)
    end: float | None = None
    attributes: dict[str, str] = field(default_factory=dict)

    def finish(self) -> None:
        self.end = time.perf_counter()
        elapsed = self.end - self.start
        METRICS.observe("cgmcp_trace_span_seconds", elapsed, span=self.name)
        _record_inproc_span(
            trace_id=self.trace_id,
            span_id=self.span_id,
            name=self.name,
            duration_ms=elapsed * 1000.0,
            attributes=self.attributes,
        )


@contextmanager
def trace_span(name: str, **attributes: str) -> Iterator[TraceSpan]:
    span = TraceSpan(
        trace_id=uuid.uuid4().hex,
        span_id=uuid.uuid4().hex[:16],
        name=name,
        attributes=attributes,
    )
    try:
        yield span
    finally:
        span.finish()


def begin_context_trace(request_id: str, **attributes: str) -> None:
    """Start root ``context`` span for a context MCP request."""
    tracer = _init_otel_tracer()
    trace_id = uuid.uuid4().hex
    _tls.trace_id = trace_id
    _tls.request_id = request_id
    _tls.phase_span = None
    attrs = {"cgmcp.request_id": request_id, **attributes}
    if tracer is None:
        _tls.root_span = None
        return
    from opentelemetry import trace

    root = tracer.start_span(
        "context",
        attributes={key: str(value) for key, value in attrs.items()},
    )
    _tls.root_span = root
    _tls.root_context = trace.set_span_in_context(root)


def phase_started(phase: str) -> None:
    tracer = _init_otel_tracer()
    root = getattr(_tls, "root_span", None)
    if tracer is None or root is None:
        _tls.phase_span = None
        return
    from opentelemetry import trace

    parent_ctx = getattr(_tls, "root_context", trace.set_span_in_context(root))
    span = tracer.start_span(
        f"context.{phase}",
        context=parent_ctx,
        attributes={
            "cgmcp.phase": phase,
            "cgmcp.request_id": str(getattr(_tls, "request_id", "")),
        },
    )
    _tls.phase_span = span


def phase_finished(phase: str, elapsed_seconds: float) -> None:
    span = getattr(_tls, "phase_span", None)
    if span is not None:
        span.set_attribute("cgmcp.duration_ms", round(elapsed_seconds * 1000.0, 3))
        span.end()
        _tls.phase_span = None
    trace_id = str(getattr(_tls, "trace_id", uuid.uuid4().hex))
    _record_inproc_span(
        trace_id=trace_id,
        span_id=uuid.uuid4().hex[:16],
        name=f"context.{phase}",
        duration_ms=elapsed_seconds * 1000.0,
        attributes={
            "cgmcp.phase": phase,
            "cgmcp.request_id": str(getattr(_tls, "request_id", "")),
        },
    )


def end_context_trace() -> None:
    root = getattr(_tls, "root_span", None)
    if root is not None:
        root.end()
    _tls.root_span = None
    _tls.phase_span = None
    _tls.root_context = None


def recent_spans(limit: int = 50) -> list[dict]:
    with _lock:
        return list(_spans[-limit:])
