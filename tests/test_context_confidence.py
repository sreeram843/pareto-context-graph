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
