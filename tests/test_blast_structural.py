from __future__ import annotations

from pareto_context_graph.blast import blast_radius
from pareto_context_graph.store import Store


def test_blast_uses_structural_edges_when_enabled(tmp_path):
    store = Store(tmp_path)
    store.upsert_file("main.py")
    store.upsert_file("helper.py")
    store.add_structural_edge("main.py", "helper.py", "calls", "INFERRED")
    store.commit()

    without = blast_radius(store, ["main.py"], min_weight=1, max_depth=1, use_structural=False)
    paths_without = {row["path"] for row in without}

    with_struct = blast_radius(store, ["main.py"], min_weight=1, max_depth=1, use_structural=True)
    paths_with = {row["path"] for row in with_struct}

    store.close()
    assert "helper.py" not in paths_without
    assert "helper.py" in paths_with
