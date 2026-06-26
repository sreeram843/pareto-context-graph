"""Tests for PCG_ABLATE_* eval ablation flags."""

import os

from pareto_context_graph.ablation import ABLATION_SIGNALS, ablation_enabled, active_ablations
from pareto_context_graph.context_pipeline import apply_ablation_to_features
from pareto_context_graph.eval import ablation_env


def test_ablation_env_toggles(monkeypatch):
    monkeypatch.delenv("PCG_ABLATE_BM25", raising=False)
    assert not ablation_enabled("bm25")
    with ablation_env("bm25"):
        assert ablation_enabled("bm25")
    assert not ablation_enabled("bm25")


def test_active_ablations_lists_enabled(monkeypatch):
    for signal in ABLATION_SIGNALS:
        monkeypatch.delenv(f"PCG_ABLATE_{signal.upper()}", raising=False)
    monkeypatch.setenv("PCG_ABLATE_EMBED", "1")
    assert active_ablations() == ["embed"]


def test_apply_ablation_to_features_zeros_columns(monkeypatch):
    monkeypatch.setenv("PCG_ABLATE_BM25", "1")
    monkeypatch.setenv("PCG_ABLATE_LEARNED", "1")
    out = apply_ablation_to_features(
        {
            "bm25": 0.9,
            "symbol": 0.5,
            "learned_boost": 0.3,
            "co_change": 4.0,
        }
    )
    assert out["bm25"] == 0.0
    assert out["learned_boost"] == 0.0
    assert out["symbol"] == 0.5


def test_ablation_signal_list_is_stable():
    assert "semantic" in ABLATION_SIGNALS
    assert "import" in ABLATION_SIGNALS
