"""Retrieval confidence surfaced to MCP callers (Phase 11+ DX)."""

from __future__ import annotations


def build_retrieval_confidence(
    *,
    sparse_graph: bool,
    truncated: bool,
    timed_out_phase: str | None,
    query_only: bool,
    orchestrator_hit_count: int,
    files_included: int,
    selective_hybrid: bool = False,
) -> dict:
    """Return a 0–1 score and human-readable degradation signals."""
    score = 1.0
    signals: list[str] = []

    if sparse_graph:
        score -= 0.25
        signals.append("sparse_graph")
    if truncated:
        score -= 0.3
        signals.append(f"truncated:{timed_out_phase or 'unknown'}")
    if query_only and orchestrator_hit_count == 0:
        score -= 0.4
        signals.append("no_orchestrator_hits")
    if files_included < 3:
        score -= 0.15
        signals.append("few_results")
    if selective_hybrid:
        signals.append("selective_hybrid")

    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        level = "high"
    elif score >= 0.45:
        level = "medium"
    else:
        level = "low"

    return {
        "score": round(score, 3),
        "level": level,
        "signals": signals,
    }
