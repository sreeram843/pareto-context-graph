from __future__ import annotations

from pareto_context_graph.orchestrator import reciprocal_rank_fusion


def test_rrf_fuses_multiple_lists():
    ranked = reciprocal_rank_fusion(
        {
            "a": [("src/a.py", 1.0), ("src/b.py", 0.5)],
            "b": [("src/b.py", 1.0), ("src/c.py", 0.8)],
        }
    )
    paths = [path for path, _score in ranked]
    assert paths[0] == "src/b.py"
    assert set(paths) == {"src/a.py", "src/b.py", "src/c.py"}


def test_rrf_respects_weights():
    ranked = reciprocal_rank_fusion(
        {
            "low": [("src/low.py", 1.0)],
            "high": [("src/high.py", 1.0)],
        },
        weights={"low": 0.1, "high": 5.0},
    )
    assert ranked[0][0] == "src/high.py"
