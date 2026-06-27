"""Retrieval confidence surfaced to MCP callers (Phase 11+ DX)."""

from __future__ import annotations

import math
from typing import Any

# Penalties tuned against fastapi+httpx golden eval (mean recall@5 ~0.74).
_CONFIDENCE_PENALTIES: dict[str, float] = {
    "sparse_graph": 0.18,
    "truncated": 0.28,
    "no_orchestrator_hits": 0.35,
    "few_results": 0.10,
}
_FEW_RESULTS_THRESHOLD = 2


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
        score -= _CONFIDENCE_PENALTIES["sparse_graph"]
        signals.append("sparse_graph")
    if truncated:
        score -= _CONFIDENCE_PENALTIES["truncated"]
        signals.append(f"truncated:{timed_out_phase or 'unknown'}")
    if query_only and orchestrator_hit_count == 0:
        score -= _CONFIDENCE_PENALTIES["no_orchestrator_hits"]
        signals.append("no_orchestrator_hits")
    if files_included < _FEW_RESULTS_THRESHOLD:
        score -= _CONFIDENCE_PENALTIES["few_results"]
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


def confidence_calibration_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Correlate retrieval_confidence.score with recall@5 on eval rows."""
    pairs: list[tuple[float, float]] = []
    for row in rows:
        confidence = row.get("retrieval_confidence") or {}
        score = confidence.get("score")
        recall = row.get("recall_at_5")
        if score is None or recall is None:
            continue
        pairs.append((float(score), float(recall)))

    if not pairs:
        return {"cases": 0, "pearson_r": 0.0, "mean_abs_error": 0.0}

    scores = [p[0] for p in pairs]
    recalls = [p[1] for p in pairs]
    mean_score = sum(scores) / len(scores)
    mean_recall = sum(recalls) / len(recalls)
    num = sum((s - mean_score) * (r - mean_recall) for s, r in pairs)
    den_score = math.sqrt(sum((s - mean_score) ** 2 for s in scores))
    den_recall = math.sqrt(sum((r - mean_recall) ** 2 for r in recalls))
    pearson = num / (den_score * den_recall) if den_score and den_recall else 0.0
    mae = sum(abs(s - r) for s, r in pairs) / len(pairs)
    return {
        "cases": len(pairs),
        "pearson_r": round(pearson, 4),
        "mean_abs_error": round(mae, 4),
        "mean_confidence": round(mean_score, 4),
        "mean_recall_at_5": round(mean_recall, 4),
    }
