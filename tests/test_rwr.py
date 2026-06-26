from __future__ import annotations

from pareto_context_graph.store import Store
from pareto_context_graph.walk import random_walk_with_restart


def test_rwr_returns_connected_nodes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    try:
        store.record_co_change("a.py", "b.py", weight=10)
        store.record_co_change("b.py", "c.py", weight=5)
        store.commit()
        store.rebuild_top_neighbours(k=10)

        scores = random_walk_with_restart(store, ["a.py"], walks=100, length=6, restart=0.2)
        assert "a.py" in scores
        assert "b.py" in scores
        assert scores["b.py"] > scores.get("c.py", 0)
    finally:
        store.close()
