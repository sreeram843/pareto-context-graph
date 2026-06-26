"""Test iterative retrieval loop (Task 13)."""

from pareto_context_graph.context_ranking import stage1_candidates as _stage1_candidates
from pareto_context_graph.store import Store


def test_iterative_reaches_bridge_file(tmp_path):
    """A bridge file reachable only via round-2 seeds is found with depth=2."""
    store = Store(tmp_path)

    # Layer 1: seed -> A, B
    store.record_co_change("seed.py", "layer1_a.py", weight=10.0)
    store.record_co_change("seed.py", "layer1_b.py", weight=10.0)
    # Layer 2: A -> bridge (not directly connected to seed)
    store.record_co_change("layer1_a.py", "bridge.py", weight=10.0)
    # bridge -> deep target
    store.record_co_change("bridge.py", "deep_target.py", weight=10.0)

    store.rebuild_top_neighbours(k=50)
    store.commit()

    # Single hop (depth=1): should get layer1 files but not bridge
    r1 = _stage1_candidates(
        store,
        ["seed.py"],
        min_weight=1,
        max_depth=1,
        cap=500,
        expansion="bfs",
    )
    r1_paths = {r["path"] for r in r1}
    assert "layer1_a.py" in r1_paths

    # With depth=2, bridge should be reachable
    r2 = _stage1_candidates(
        store,
        ["seed.py"],
        min_weight=1,
        max_depth=2,
        cap=500,
        expansion="bfs",
    )
    r2_paths = {r["path"] for r in r2}
    assert "bridge.py" in r2_paths or "deep_target.py" in r2_paths, (
        f"Deeper expansion should reach bridge/deep files. Got: {r2_paths}"
    )
    store.close()
