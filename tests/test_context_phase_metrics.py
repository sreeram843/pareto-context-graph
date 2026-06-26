"""Tests for context pipeline phase latency histograms (Phase 14.3)."""

from __future__ import annotations

import json

from pareto_context_graph.graph import build_graph
from pareto_context_graph.metrics import CONTEXT_PHASES, METRICS, ContextPhaseTracker
from pareto_context_graph.server import _handle_tool_call


def test_context_phase_tracker_records_phases():
    tracker = ContextPhaseTracker()
    tracker.enter("retrieve")
    tracker.enter("hybrid")
    tracker.enter("rank")
    tracker.close_active()

    snap = METRICS.snapshot()
    hist = snap["histograms"]
    assert any("cgmcp_context_phase_latency_seconds" in key for key in hist)
    retrieve_key = 'cgmcp_context_phase_latency_seconds{phase="retrieve"}'
    assert retrieve_key in hist
    assert hist[retrieve_key]["count"] >= 1.0


def test_prometheus_text_exports_phase_quantiles():
    METRICS.observe("cgmcp_context_phase_latency_seconds", 0.01, phase="pack")
    text = METRICS.prometheus_text()
    assert "cgmcp_context_phase_latency_seconds" in text
    assert 'quantile="0.95"' in text
    assert "cgmcp_context_phase_latency_seconds_count" in text
    assert "cgmcp_context_phase_latency_seconds_sum" in text


def test_context_phases_match_pipeline():
    assert CONTEXT_PHASES == frozenset(
        {"retrieve", "hybrid", "semantic", "rank", "pack", "filter"}
    )


def test_context_request_records_phase_histograms(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=200, files=40, seed=9)
    store = build_graph(repo, max_commits=300)
    store.close()

    before = METRICS.snapshot()["histograms"]
    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": ["src/f0.py"],
                "query": "logging handler",
                "tier": 1,
                "token_budget": 8000,
                "timeout_ms": 5000,
                "session_memory": False,
            },
        )
    )
    after = METRICS.snapshot()["histograms"]

    assert "error" not in payload
    recorded = {
        key.split('phase="', 1)[1].split('"', 1)[0]
        for key in after
        if key.startswith("cgmcp_context_phase_latency_seconds{")
        and (key not in before or after[key]["count"] > before.get(key, {}).get("count", 0))
    }
    assert "retrieve" in recorded
    assert "pack" in recorded
