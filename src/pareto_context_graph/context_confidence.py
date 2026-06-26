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
    fallbacks: dict[str, object] | None = None,
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

    fb = fallbacks or {}
    if fb.get("bm25_empty_fallback"):
        signals.append("fallback:bm25_to_tfidf")
    backend = fb.get("backend") or fb.get("semantic_backend")
    if backend and backend != "bm25":
        signals.append(f"semantic:{backend}")
    if fb.get("leiden_fallback"):
        signals.append("fallback:leiden_to_components")
    if fb.get("ablations"):
        ablated = fb["ablations"]
        if isinstance(ablated, list):
            signals.append(f"ablated:{','.join(str(x) for x in ablated)}")

    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        level = "high"
    elif score >= 0.45:
        level = "medium"
    else:
        level = "low"

    payload: dict = {
        "score": round(score, 3),
        "level": level,
        "signals": signals,
    }
    if fb:
        payload["fallbacks"] = fb
    return payload
