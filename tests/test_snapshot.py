from __future__ import annotations

from pareto_context_graph.graph import build_graph
from pareto_context_graph.snapshot import export_snapshot, import_snapshot
from pareto_context_graph.store import Store


def _snapshot_rows(repo):
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


def test_snapshot_round_trip(synthetic_repo_factory, tmp_path):
    repo = synthetic_repo_factory(commits=120, files=20, seed=9)
    store = build_graph(repo, max_commits=400)
    store.close()

    before = _snapshot_rows(repo)
    archive = tmp_path / "graph.tar.gz"
    export_snapshot(repo, archive)

    # wipe and restore
    store = Store(repo)
    store.clear()
    store.close()

    import_snapshot(repo, archive)
    after = _snapshot_rows(repo)
    assert before == after
