"""Test two-stage retrieval pipeline (Task 8)."""
from pareto_context_graph.context_ranking import stage1_candidates as _stage1_candidates
from pareto_context_graph.store import Store


def test_stage1_respects_cap(tmp_path):
    """Stage 1 must return at most `cap` candidates."""
    store = Store(tmp_path)

    seed = "core/main.py"
    # Create a large graph: seed connects to 600 files
    for i in range(600):
        target = f"module/file_{i}.py"
        store.record_co_change(seed, target, weight=float(i % 10 + 1))

    store.rebuild_top_neighbours(k=50)
    store.commit()

    cap = 200
    results = _stage1_candidates(
        store,
        [seed],
        min_weight=1,
        max_depth=1,
        cap=cap,
        expansion="bfs",
    )

    assert len(results) <= cap, f"Stage 1 returned {len(results)} candidates, expected <= {cap}"
    assert len(results) > 0, "Stage 1 should return some candidates"
    store.close()


def test_stage1_small_repo_returns_all(tmp_path):
    """When total candidates <= cap, stage 1 returns everything."""
    store = Store(tmp_path)

    seed = "core/main.py"
    targets = [f"lib/helper_{i}.py" for i in range(10)]
    for t in targets:
        store.record_co_change(seed, t, weight=5.0)

    store.rebuild_top_neighbours(k=50)
    store.commit()

    results = _stage1_candidates(
        store,
        [seed],
        min_weight=1,
        max_depth=1,
        cap=500,
        expansion="bfs",
    )

    assert len(results) == len(targets)
    store.close()
