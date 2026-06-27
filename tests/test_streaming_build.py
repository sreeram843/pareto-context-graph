from __future__ import annotations

from pareto_context_graph.graph import build_graph
from pareto_context_graph.store import Store


def _snapshot_pairs(repo):
    store = Store(repo)
    try:
        rows = store.conn.execute(
            """SELECT f1.path, f2.path, ROUND(c.weight, 6)
               FROM co_changes c
               JOIN files f1 ON f1.id = c.file_a
               JOIN files f2 ON f2.id = c.file_b
               ORDER BY f1.path, f2.path"""
        ).fetchall()
        return rows
    finally:
        store.close()


def test_streaming_matches_legacy(synthetic_repo_factory, monkeypatch):
    repo = synthetic_repo_factory(commits=200, files=25, seed=17)

    monkeypatch.setenv("CODE_GRAPH_LEGACY_BUILD", "1")
    store = build_graph(repo, max_commits=1000)
    store.close()
    legacy_rows = _snapshot_pairs(repo)

    monkeypatch.delenv("CODE_GRAPH_LEGACY_BUILD", raising=False)
    store = build_graph(repo, max_commits=1000)
    store.close()
    streaming_rows = _snapshot_pairs(repo)

    assert legacy_rows == streaming_rows
