"""Individual retrieval signals for multi-source fusion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .embed import query_embedding_scores
from .store import Store
from .taxonomy import is_concept_query, is_noise_path, looks_like_symbol

__all__ = [
    "RETRIEVERS",
    "RetrievalContext",
    "bm25_retriever",
    "cochange_retriever",
    "embed_retriever",
    "is_concept_query",
    "looks_like_symbol",
    "path_retriever",
    "symbol_retriever",
]


@dataclass
class RetrievalContext:
    repo_root: Path
    store: Store
    query: str
    files: list[str]


def path_retriever(ctx: RetrievalContext, *, limit: int = 30) -> list[tuple[str, float]]:
    if not ctx.query.strip():
        return []
    paths = ctx.store.search_files(ctx.query, limit=limit)
    ranked = [(path, float(limit - idx)) for idx, path in enumerate(paths)]
    return _deprioritize_test_paths(ranked, limit=limit)


def symbol_retriever(ctx: RetrievalContext, *, limit: int = 30) -> list[tuple[str, float]]:
    if not ctx.query.strip():
        return []
    rows = ctx.store.search_symbols(ctx.query, limit=limit)
    by_path: dict[str, float] = {}
    for path, score, _symbol, _line in rows:
        by_path[path] = max(by_path.get(path, 0.0), score)
    ranked = sorted(by_path.items(), key=lambda item: -item[1])
    return _deprioritize_test_paths(ranked, limit=limit)


def bm25_retriever(ctx: RetrievalContext, *, limit: int = 30) -> list[tuple[str, float]]:
    if not ctx.query.strip():
        return []
    if ctx.store.has_search_index():
        ranked = ctx.store.search_content_bm25(ctx.query, limit=limit)
        return _deprioritize_test_paths(ranked, limit=limit)
    return []


def embed_retriever(ctx: RetrievalContext, *, limit: int = 30) -> list[tuple[str, float]]:
    if not ctx.query.strip():
        return []
    paths = ctx.store.all_files()
    scores = query_embedding_scores(ctx.repo_root, ctx.query, paths)
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    return ranked[:limit]


def cochange_retriever(ctx: RetrievalContext, *, limit: int = 30) -> list[tuple[str, float]]:
    if not ctx.files:
        return []
    merged: dict[str, float] = {}
    for seed in ctx.files:
        for path, weight in ctx.store.neighbours(seed, min_weight=1):
            merged[path] = max(merged.get(path, 0.0), float(weight))
    ranked = sorted(merged.items(), key=lambda item: -item[1])
    return ranked[:limit]


def _deprioritize_test_paths(
    ranked: list[tuple[str, float]], *, limit: int
) -> list[tuple[str, float]]:
    clean = [(p, s) for p, s in ranked if not is_noise_path(p)]
    if clean:
        return clean[:limit]
    return ranked[:limit]


RETRIEVERS = {
    "path": path_retriever,
    "symbol": symbol_retriever,
    "bm25": bm25_retriever,
    "embed": embed_retriever,
    "co_change": cochange_retriever,
}
