"""Reciprocal-rank fusion orchestrator for query-first retrieval."""

from __future__ import annotations

from pathlib import Path

from .retrievers import RETRIEVERS, RetrievalContext
from .store import Store
from .taxonomy import is_concept_query, looks_like_symbol

RRF_K = 60

_WEIGHTS: dict[str, dict[str, float]] = {
    "default": {
        "path": 1.0,
        "symbol": 1.2,
        "bm25": 1.5,
        "embed": 0.6,
        "co_change": 1.0,
    },
    "seed_only": {
        "path": 0.5,
        "symbol": 0.5,
        "bm25": 0.5,
        "embed": 0.3,
        "co_change": 2.0,
    },
    "symbol_like": {
        "path": 0.6,
        "symbol": 2.5,
        "bm25": 1.0,
        "embed": 0.4,
        "co_change": 0.8,
    },
    "concept": {
        "path": 0.8,
        "symbol": 1.0,
        "bm25": 2.0,
        "embed": 1.0,
        "co_change": 0.5,
    },
}


def plan(query: str, files: list[str]) -> dict:
    """Choose retriever weights from query shape and seed presence."""
    if files and not query.strip():
        intent = "seed_only"
    elif looks_like_symbol(query):
        intent = "symbol_like"
    elif is_concept_query(query):
        intent = "concept"
    else:
        intent = "default"
    return {"intent": intent, "weights": dict(_WEIGHTS[intent])}


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[tuple[str, float]]],
    *,
    weights: dict[str, float] | None = None,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    weights = weights or {}
    fused: dict[str, float] = {}
    for source, ranked in ranked_lists.items():
        weight = weights.get(source, 1.0)
        for rank, (path, _raw) in enumerate(ranked, start=1):
            fused[path] = fused.get(path, 0.0) + weight * (1.0 / (k + rank))
    return sorted(fused.items(), key=lambda item: -item[1])


def retrieve(
    repo_root: Path,
    store: Store,
    query: str,
    files: list[str],
    *,
    limit: int = 50,
) -> list[dict]:
    """Fuse retriever outputs into a ranked candidate pool."""
    ctx = RetrievalContext(repo_root=repo_root, store=store, query=query, files=files)
    plan_info = plan(query, files)
    weights = plan_info["weights"]

    ranked_lists: dict[str, list[tuple[str, float]]] = {}
    feature_scores: dict[str, dict[str, float]] = {}
    for name, fn in RETRIEVERS.items():
        ranked = fn(ctx, limit=max(limit, 30))
        ranked_lists[name] = ranked
        for path, score in ranked:
            feature_scores.setdefault(path, {})[name] = round(float(score), 4)

    fused = reciprocal_rank_fusion(ranked_lists, weights=weights)[:limit]
    return [
        {
            "path": path,
            "score": round(score, 6),
            "features": feature_scores.get(path, {}),
            "intent": plan_info["intent"],
        }
        for path, score in fused
    ]
