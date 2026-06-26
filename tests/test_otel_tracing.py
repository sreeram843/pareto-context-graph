"""Tests for OTLP context phase tracing (Phase 14.1)."""

from __future__ import annotations

import pytest

otel = pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pareto_context_graph.metrics import ContextPhaseTracker
from pareto_context_graph.tracing import (
    begin_context_trace,
    configure_tracer_provider,
    end_context_trace,
    otel_enabled,
    recent_spans,
    reset_tracing_for_test,
)


@pytest.fixture(autouse=True)
def _reset_tracing(monkeypatch):
    reset_tracing_for_test()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("PCG_OTEL_ENDPOINT", raising=False)
    yield
    reset_tracing_for_test()


def test_context_phase_tracker_emits_inproc_trace_spans():
    begin_context_trace("req-trace-1", query="auth middleware")
    tracker = ContextPhaseTracker()
    tracker.enter("retrieve")
    tracker.enter("rank")
    tracker.close_active()
    end_context_trace()

    names = [span["name"] for span in recent_spans()]
    assert "context.retrieve" in names
    assert "context.rank" in names


def test_otel_exporter_records_context_phases(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    configure_tracer_provider(provider)
    monkeypatch.setenv("PCG_OTEL_ENDPOINT", "http://127.0.0.1:4318")
    assert otel_enabled() is True

    begin_context_trace("req-otel-1", query="kubernetes scheduler")
    tracker = ContextPhaseTracker()
    tracker.enter("retrieve")
    tracker.enter("pack")
    tracker.close_active()
    end_context_trace()

    names = [span.name for span in exporter.get_finished_spans()]
    assert "context" in names
    assert "context.retrieve" in names
    assert "context.pack" in names
