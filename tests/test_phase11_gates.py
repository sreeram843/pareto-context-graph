"""Unit tests for Phase 11 eval gates."""

from __future__ import annotations

from pareto_context_graph.eval import (
    PHASE11_CONCEPT_RECALL_LIFT,
    check_phase11_fastapi_concept_gate,
    load_phase9_fastapi_concept_baseline,
)


def test_phase11_concept_gate_passes_at_target():
    baseline = load_phase9_fastapi_concept_baseline()
    target = baseline + PHASE11_CONCEPT_RECALL_LIFT
    rows = [
        {
            "repo_key": "fastapi",
            "category": "concept",
            "recall_at_5": target,
            "mrr": 0.8,
            "ndcg_at_10": 0.8,
            "tokens_used": 1000,
            "token_efficiency": 0.001,
            "budget_honesty": 1.0,
            "payload_honesty": 1.0,
            "reduction_vs_corpus": 10.0,
            "reduction_vs_agent": 10.0,
        }
    ]
    gate = check_phase11_fastapi_concept_gate(rows)
    assert gate["passed"] is True
    assert gate["current_recall_at_5"] == round(target, 4)


def test_phase11_concept_gate_fails_below_target():
    baseline = load_phase9_fastapi_concept_baseline()
    rows = [
        {
            "repo_key": "fastapi",
            "category": "concept",
            "recall_at_5": baseline,
            "mrr": 0.5,
            "ndcg_at_10": 0.5,
            "tokens_used": 1000,
            "token_efficiency": 0.001,
            "budget_honesty": 1.0,
            "payload_honesty": 1.0,
            "reduction_vs_corpus": 10.0,
            "reduction_vs_agent": 10.0,
        }
    ]
    gate = check_phase11_fastapi_concept_gate(rows)
    assert gate["passed"] is False
