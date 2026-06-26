"""Tests for compress_pick_reason (tier-1 explainability)."""

from pareto_context_graph.context_ranking import compress_pick_reason, entry_diagnostics


def test_compress_pick_reason_includes_signal_and_weight():
    row = {
        "path": "src/auth/service.py",
        "weight": 12,
        "signal": "semantic",
        "_relevance": 42.0,
    }
    reason = compress_pick_reason(
        row,
        files=["src/auth/router.py"],
        node_degrees={"src/auth/service.py": 3},
        learned={},
        embed_scores={},
        hub_penalty_strength=1.0,
    )
    assert "semantic" in reason
    assert "co-change w=12" in reason


def test_compress_pick_reason_falls_back_to_score():
    row = {"path": "src/main.py", "weight": 1, "_relevance": 5.5}
    reason = compress_pick_reason(
        row,
        files=[],
        node_degrees={},
        learned={},
        embed_scores={},
        hub_penalty_strength=1.0,
    )
    assert reason == "score=5.5"


def test_entry_diagnostics_vs_pick_reason_token_budget():
    row = {
        "path": "src/auth/service.py",
        "weight": 12,
        "signal": "import",
        "_features": {"bm25": 0.8, "symbol": 0.2},
        "_relevance": 20.0,
    }
    ctx = {
        "files": ["src/auth/router.py"],
        "node_degrees": {"src/auth/service.py": 2},
        "learned": {},
        "embed_scores": {},
        "hub_penalty_strength": 1.0,
    }
    diag = entry_diagnostics(row, **ctx)
    reason = compress_pick_reason(row, **ctx)
    assert len(reason) < len(str(diag))
    assert "import" in reason
