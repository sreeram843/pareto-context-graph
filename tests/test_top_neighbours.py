from __future__ import annotations

from code_graph_mcp.store import Store


def test_top_neighbour_cache_ordering(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    try:
        store.record_co_change("a.py", "b.py", weight=5.0)
        store.record_co_change("a.py", "c.py", weight=3.0)
        store.record_co_change("a.py", "d.py", weight=1.0)
        store.commit()
        store.rebuild_top_neighbours(k=2)

        top = store.top_neighbours("a.py", limit=5)
        assert len(top) == 2
        assert top[0][0] == "b.py"
        assert top[1][0] == "c.py"
    finally:
        store.close()
