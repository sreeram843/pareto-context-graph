"""Tests for learned rankers (logistic + optional LambdaMART)."""

from __future__ import annotations

import json

import pytest

from pareto_context_graph.ranker import (
    FEATURE_KEYS,
    LogisticRanker,
    apply_ranker_boost,
    lightgbm_available,
    load_ranker,
    save_ranker,
    train_best_ranker,
    train_lambdamart_ranker,
    train_logistic_ranker,
)


def _labeled_events() -> list[dict]:
    pools = [
        {
            "kind": "context_request",
            "request_id": "r1",
            "query": "auth",
            "candidates": [
                {"path": "good.py", "features": {"rank_score": 4.0, "co_change": 3.0, "bm25": 2.0}},
                {"path": "bad.py", "features": {"rank_score": 9.0, "co_change": 1.0, "bm25": 0.5}},
                {"path": "good2.py", "features": {"rank_score": 3.5, "co_change": 2.5, "bm25": 1.5}},
                {"path": "bad2.py", "features": {"rank_score": 8.0, "co_change": 0.5, "bm25": 0.2}},
            ],
        },
        {
            "kind": "context_request",
            "request_id": "r2",
            "query": "db",
            "candidates": [
                {"path": "db.py", "features": {"rank_score": 5.0, "co_change": 4.0}},
                {"path": "noise.py", "features": {"rank_score": 7.0, "co_change": 0.5}},
                {"path": "db2.py", "features": {"rank_score": 4.5, "co_change": 3.0}},
                {"path": "noise2.py", "features": {"rank_score": 6.5, "co_change": 0.3}},
            ],
        },
    ]
    labels = [
        {"kind": "accept", "request_id": "r1", "path": "good.py"},
        {"kind": "accept", "request_id": "r1", "path": "good2.py"},
        {"kind": "reject", "request_id": "r1", "path": "bad.py"},
        {"kind": "reject", "request_id": "r1", "path": "bad2.py"},
        {"kind": "accept", "request_id": "r2", "path": "db.py"},
        {"kind": "accept", "request_id": "r2", "path": "db2.py"},
        {"kind": "reject", "request_id": "r2", "path": "noise.py"},
        {"kind": "reject", "request_id": "r2", "path": "noise2.py"},
    ]
    return pools + labels


def test_train_logistic_ranker_returns_model():
    events = _labeled_events()
    ranker = train_logistic_ranker(events, epochs=80)
    assert ranker is not None
    score_good = ranker.score({"rank_score": 4.0, "co_change": 3.0, "bm25": 2.0})
    score_bad = ranker.score({"rank_score": 9.0, "co_change": 1.0, "bm25": 0.5})
    assert score_good != score_bad


def test_logistic_roundtrip(tmp_path):
    events = _labeled_events()
    ranker = train_logistic_ranker(events)
    assert ranker is not None
    path = save_ranker(tmp_path, ranker)
    loaded = load_ranker(tmp_path)
    assert loaded is not None
    assert isinstance(loaded, LogisticRanker)
    payload = json.loads(path.read_text())
    assert payload["model"] == "logistic_v1"


def test_train_best_ranker_auto_falls_back_without_lightgbm(monkeypatch):
    events = _labeled_events()
    monkeypatch.setattr("pareto_context_graph.ranker.train_lambdamart_ranker", lambda _e: None)
    ranker = train_best_ranker(events, prefer="auto")
    assert ranker is not None
    assert isinstance(ranker, LogisticRanker)


def test_apply_ranker_boost_blends():
    ranker = LogisticRanker(weights={"rank_score": 1.0}, bias=0.5)
    boosted = apply_ranker_boost(10.0, {"rank_score": 2.0}, ranker, alpha=0.75)
    assert boosted > 10.0


@pytest.mark.skipif(not lightgbm_available(), reason="lightgbm not installed")
def test_lambdamart_train_save_load(tmp_path):
    events = _labeled_events()
    ranker = train_lambdamart_ranker(events)
    assert ranker is not None
    save_ranker(tmp_path, ranker)
    loaded = load_ranker(tmp_path)
    assert loaded is not None
    score = loaded.score({key: 1.0 for key in FEATURE_KEYS})
    assert isinstance(score, float)


@pytest.mark.skipif(not lightgbm_available(), reason="lightgbm not installed")
def test_train_best_ranker_prefers_lambdamart(tmp_path):
    events = _labeled_events()
    ranker = train_best_ranker(events, prefer="auto")
    assert ranker is not None
    save_ranker(tmp_path, ranker)
    payload = json.loads((tmp_path / ".pareto-context-graph" / "ranker.json").read_text())
    assert payload["model"] == "lambdamart_v1"
    assert (tmp_path / ".pareto-context-graph" / "ranker.lgb.txt").exists()
