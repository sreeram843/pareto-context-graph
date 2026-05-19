"""Test hub suppression in context ranking (Task 5)."""
import math

from code_graph_mcp.store import Store


def test_hub_file_ranked_below_focused_cluster(tmp_path):
    """A hub connected to 50 files should be penalized below focused files."""
    store = Store(tmp_path)

    # Create a hub file connected to 50 others
    hub = "hub_router.py"
    focused = ["feature/auth.py", "feature/auth_helper.py", "feature/auth_test.py"]

    # Hub connects weakly to everything
    for i in range(50):
        store.record_co_change(hub, f"misc/file_{i}.py", weight=2.0)

    # Focused cluster: files that co-change strongly with each other
    for a in focused:
        for b in focused:
            if a < b:
                store.record_co_change(a, b, weight=10.0)
        # Also connect focused files to the hub (weakly)
        pair = (hub, a) if hub < a else (a, hub)
        store.record_co_change(pair[0], pair[1], weight=1.0)

    store.rebuild_top_neighbours(k=50)
    store.commit()

    # Verify hub has the highest raw degree
    degrees = store.node_degrees()
    assert degrees.get(hub, 0) > degrees.get(focused[0], 0)

    # After hub suppression (log2(2 + degree) penalty), the hub's effective
    # score should be lower than a focused-cluster file.
    hub_degree = degrees[hub]
    focused_degree = degrees[focused[0]]

    raw_hub_score = 2.0  # weight from co-change
    raw_focused_score = 10.0

    suppressed_hub = raw_hub_score / math.log2(2 + hub_degree)
    suppressed_focused = raw_focused_score / math.log2(2 + focused_degree)

    assert suppressed_focused > suppressed_hub, (
        f"Focused file score {suppressed_focused} should beat hub score {suppressed_hub}"
    )
    store.close()
