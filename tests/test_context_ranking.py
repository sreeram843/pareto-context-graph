"""Tests for context ranking helpers."""

from pareto_context_graph.context_ranking import (
    apply_file_class_weight,
    build_mirror_groups,
    collapse_signatures,
    filter_signatures,
    mmr_select,
    mmr_select_with_protected_head,
    rrf_rank_relevance,
)


def test_rrf_rank_relevance_ranks_strong_cochange_first():
    cands = [
        {"path": "src/strong.py", "weight": 10.0},
        {"path": "src/weak.py", "weight": 1.0},
    ]
    fused = rrf_rank_relevance(
        cands, files=["src/seed.py"], node_degrees={"src/strong.py": 5, "src/weak.py": 5},
        embed_scores={}, query="",
    )
    assert fused["src/strong.py"] > fused["src/weak.py"]


def test_rrf_rank_relevance_rewards_query_term_match():
    cands = [
        {"path": "src/auth_handler.py", "weight": 1.0},
        {"path": "src/unrelated.py", "weight": 1.0},
    ]
    fused = rrf_rank_relevance(
        cands, files=[], node_degrees={}, embed_scores={}, query="auth flow",
    )
    assert fused["src/auth_handler.py"] > fused["src/unrelated.py"]


def test_collapse_signatures_dedups_exact():
    sigs = ["def foo(x):", "def foo(x):", "def bar(y):"]
    out = collapse_signatures(sigs)
    assert out == ["def foo(x):", "def bar(y):"]


def test_collapse_signatures_collapses_overloads():
    sigs = [f"def handle(x: T{i}) -> R{i}:" for i in range(6)]
    out = collapse_signatures(sigs, max_per_name=2)
    assert out[0] == "def handle(x: T0) -> R0:"
    assert out[1] == "def handle(x: T1) -> R1:"
    assert out[2] == "# … +4 more handle(...) overloads"
    assert len(out) == 3


def test_collapse_signatures_preserves_unnamed_and_disable():
    sigs = ["def foo(a):", "# a comment", "def foo(b):", "def foo(c):"]
    out = collapse_signatures(sigs, max_per_name=2)
    assert "# a comment" in out
    assert collapse_signatures(sigs, max_per_name=0) == sigs


def test_filter_signatures_applies_collapse():
    sigs = [f"def overload(x{i}):" for i in range(10)] + ["def other(y):"]
    out = filter_signatures(sigs, "none")
    assert any("more overload(...) overloads" in s for s in out)
    assert "def other(y):" in out
    assert len(out) < 11


def test_apply_file_class_weight_doc_deprioritized():
    score = apply_file_class_weight(10.0, "docs/guide.md", "endpoint")
    assert score < 10.0


def test_apply_file_class_weight_openapi_boosts_openapi_dir():
    boosted = apply_file_class_weight(10.0, "fastapi/openapi/utils.py", "openapi")
    neutral = apply_file_class_weight(10.0, "fastapi/applications.py", "openapi")
    assert boosted > 10.0
    assert neutral == 10.0


def test_apply_file_class_weight_openapi_downweight_ablation(monkeypatch):
    monkeypatch.setenv("PCG_ABLATE_OPENAPI_DOWNWEIGHT", "1")
    demoted = apply_file_class_weight(10.0, "fastapi/applications.py", "openapi")
    assert demoted < 10.0
    monkeypatch.delenv("PCG_ABLATE_OPENAPI_DOWNWEIGHT", raising=False)


def test_build_mirror_groups_tracks_test_and_impl():
    groups = build_mirror_groups(["app/user.py", "spec/user_spec.rb"])
    assert len(groups) == 1
    entry = next(iter(groups.values()))
    assert entry["has_test"] and entry["has_non_test"]


def test_mmr_select_limits_results():
    candidates = [{"path": f"src/f{i}.py", "weight": 10 - i, "_symbols": []} for i in range(5)]
    selected = mmr_select(candidates, limit=2, mmr_lambda=0.7)
    assert len(selected) == 2


def test_mmr_select_with_protected_head_keeps_top_relevance():
    candidates = [
        {"path": "pkg/a.py", "_relevance": 100.0, "_symbols": []},
        {"path": "pkg/b.py", "_relevance": 90.0, "_symbols": []},
        {"path": "pkg/c.py", "_relevance": 80.0, "_symbols": []},
        {"path": "pkg/d.py", "_relevance": 10.0, "_symbols": []},
    ]
    selected = mmr_select_with_protected_head(candidates, limit=4, mmr_lambda=0.3, protect_top=2)
    assert [row["path"] for row in selected[:2]] == ["pkg/a.py", "pkg/b.py"]
