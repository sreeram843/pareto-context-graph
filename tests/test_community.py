from __future__ import annotations

from pareto_context_graph.community import detect_communities
from pareto_context_graph.store import Store


def test_detect_communities_connected_components(tmp_path):
    store = Store(tmp_path)
    for path in ("a.py", "b.py", "c.py", "d.py"):
        store.upsert_file(path)
    store.record_co_change("a.py", "b.py", weight=5)
    store.record_co_change("b.py", "c.py", weight=4)
    store.record_co_change("d.py", "e.py", weight=5)
    store.commit()

    payload = detect_communities(store, use_leiden=False, min_weight=1)
    store.close()

    assert payload["method"] == "connected_components"
    assert payload["total_communities"] >= 1
    assert payload["communities"][0]["size"] >= 2


def test_detect_communities_leiden_when_available(tmp_path):
    try:
        import igraph  # noqa: F401
    except ImportError:
        return

    store = Store(tmp_path)
    paths = [f"src/f{i}.py" for i in range(6)]
    for path in paths:
        store.upsert_file(path)
    store.record_co_change("src/f0.py", "src/f1.py", weight=5)
    store.record_co_change("src/f1.py", "src/f2.py", weight=4)
    store.record_co_change("src/f3.py", "src/f4.py", weight=5)
    store.record_co_change("src/f4.py", "src/f5.py", weight=4)
    store.commit()

    payload = detect_communities(store, use_leiden=True, min_weight=1, profile_name="tiny")
    store.close()

    assert payload["method"] == "leiden"
    assert payload["total_communities"] >= 2
