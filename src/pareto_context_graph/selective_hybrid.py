"""Hybrid retrieval policy for large graphs (Phase 11.2)."""

from __future__ import annotations

DEFAULT_SEMANTIC_TOP_N = 15
LARGE_GRAPH_SEMANTIC_TOP_N = 10


def allow_seed_hybrid(*, high_fanout: bool, large_graph: bool) -> bool:
    """Import/naming/dbt hybrid — disabled on high-fanout or large graphs."""
    return not high_fanout and not large_graph


def allow_semantic_hybrid(
    *,
    query: str,
    high_fanout: bool,
    large_graph: bool,
    query_only: bool,
) -> bool:
    """BM25 / TF-IDF content match — query-only on large graphs; always on smaller repos."""
    if not query.strip() or high_fanout:
        return False
    if large_graph:
        return query_only
    return True


def semantic_top_n(*, large_graph: bool, query_only: bool) -> int:
    if large_graph and query_only:
        return LARGE_GRAPH_SEMANTIC_TOP_N
    return DEFAULT_SEMANTIC_TOP_N


def prefer_bm25_for_semantic(*, large_graph: bool, query_only: bool) -> bool:
    """Prefer SQLite BM25 over in-memory TF-IDF on large query-first paths."""
    return large_graph and query_only
