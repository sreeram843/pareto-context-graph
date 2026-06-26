"""Tests for selective hybrid policy (Phase 11.2)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pareto_context_graph.selective_hybrid import (
    LARGE_GRAPH_SEMANTIC_TOP_N,
    allow_seed_hybrid,
    allow_semantic_hybrid,
    prefer_bm25_for_semantic,
    semantic_top_n,
)
from pareto_context_graph.context_ranking import (
    semantic_search_capped_tfidf as _semantic_search_capped_tfidf,
)


def test_allow_seed_hybrid():
    assert allow_seed_hybrid(high_fanout=False, large_graph=False) is True
    assert allow_seed_hybrid(high_fanout=True, large_graph=False) is False
    assert allow_seed_hybrid(high_fanout=False, large_graph=True) is False


def test_allow_semantic_hybrid_large_query_only():
    assert (
        allow_semantic_hybrid(
            query="kubelet pod",
            high_fanout=False,
            large_graph=True,
            query_only=True,
        )
        is True
    )
    assert (
        allow_semantic_hybrid(
            query="kubelet",
            high_fanout=False,
            large_graph=True,
            query_only=False,
        )
        is False
    )
    assert (
        allow_semantic_hybrid(
            query="routing",
            high_fanout=False,
            large_graph=False,
            query_only=False,
        )
        is True
    )
    assert (
        allow_semantic_hybrid(
            query="routing",
            high_fanout=True,
            large_graph=False,
            query_only=False,
        )
        is False
    )


def test_semantic_top_n_and_bm25_preference():
    assert semantic_top_n(large_graph=True, query_only=True) == LARGE_GRAPH_SEMANTIC_TOP_N
    assert semantic_top_n(large_graph=False, query_only=False) == 15
    assert prefer_bm25_for_semantic(large_graph=True, query_only=True) is True
    assert prefer_bm25_for_semantic(large_graph=True, query_only=False) is False


def test_semantic_search_capped_tfidf(tmp_path: Path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "auth.py").write_text(
        "def authenticate_user(password):\n    return verify(password)\n",
        encoding="utf-8",
    )
    (pkg / "noise.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")

    store = MagicMock()
    store.search_files.return_value = ["pkg/auth.py", "pkg/noise.py"]
    store.search_symbols.return_value = []

    hits = _semantic_search_capped_tfidf(repo, store, "authenticate password", top_n=2)
    paths = [path for path, _score in hits]
    assert "pkg/auth.py" in paths


KUBERNETES_BENCH = Path("bench/kubernetes")
KUBERNETES_GRAPH = KUBERNETES_BENCH / ".pareto-context-graph" / "graph.db"


@pytest.mark.skipif(
    not KUBERNETES_GRAPH.exists(),
    reason="kubernetes bench graph not built",
)
def test_kubernetes_query_first_sets_selective_hybrid_flag():
    from pareto_context_graph.server import _handle_tool_call

    raw = _handle_tool_call(
        KUBERNETES_BENCH.resolve(),
        "pareto_context_graph",
        {
            "command": "context",
            "query": "kubelet pod scheduling",
            "tier": 1,
            "token_budget": 8000,
            "timeout_ms": 8000,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    assert "error" not in payload
    assert payload.get("query_first") is True
    assert payload.get("selective_hybrid") is True
    assert len(payload.get("context_files", [])) > 0
