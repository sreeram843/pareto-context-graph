"""Tests for retrieval confidence scoring."""

from pareto_context_graph.context_confidence import build_retrieval_confidence


def test_high_confidence_query_with_hits():
    payload = build_retrieval_confidence(
        sparse_graph=False,
        truncated=False,
        timed_out_phase="",
        query_only=True,
        orchestrator_hit_count=5,
        files_included=8,
    )
    assert payload["level"] == "high"
    assert payload["score"] >= 0.75


def test_low_confidence_sparse_no_hits():
    payload = build_retrieval_confidence(
        sparse_graph=True,
        truncated=True,
        timed_out_phase="rank",
        query_only=True,
        orchestrator_hit_count=0,
        files_included=1,
    )
    assert payload["level"] == "low"
    assert "sparse_graph" in payload["signals"]
    assert "no_orchestrator_hits" in payload["signals"]


def test_fallback_telemetry_in_signals():
    payload = build_retrieval_confidence(
        sparse_graph=False,
        truncated=False,
        timed_out_phase="",
        query_only=False,
        orchestrator_hit_count=3,
        files_included=5,
        fallbacks={
            "backend": "tfidf_capped",
            "bm25_empty_fallback": True,
            "leiden_fallback": True,
            "ablations": ["embed"],
        },
    )
    assert "fallback:bm25_to_tfidf" in payload["signals"]
    assert "semantic:tfidf_capped" in payload["signals"]
    assert payload["fallbacks"]["leiden_fallback"] is True
