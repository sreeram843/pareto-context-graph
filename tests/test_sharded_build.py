from __future__ import annotations

from code_graph_mcp.graph import build_graph_sharded
from code_graph_mcp.store import Store


def _rows(repo):
    store = Store(repo)
    try:
        return store.conn.execute(
            """SELECT f1.path, f2.path, ROUND(c.weight, 6)
               FROM co_changes c
               JOIN files f1 ON f1.id = c.file_a
               JOIN files f2 ON f2.id = c.file_b
               ORDER BY f1.path, f2.path"""
        ).fetchall()
    finally:
        store.close()


def test_sharded_matches_single(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=300, files=25, seed=19)

    store = build_graph_sharded(repo, max_commits=1000, shards=1)
    store.close()
    one = _rows(repo)

    store = build_graph_sharded(repo, max_commits=1000, shards=4)
    store.close()
    four = _rows(repo)

    assert one == four
