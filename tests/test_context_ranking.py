"""Tests for context ranking helpers."""

from pareto_context_graph.context_ranking import (
    apply_file_class_weight,
    build_mirror_groups,
    mmr_select,
)


def test_apply_file_class_weight_doc_deprioritized():
    score = apply_file_class_weight(10.0, "docs/guide.md", "endpoint")
    assert score < 10.0


def test_apply_file_class_weight_openapi_boosts_openapi_dir():
    boosted = apply_file_class_weight(10.0, "fastapi/openapi/utils.py", "openapi")
    demoted = apply_file_class_weight(10.0, "fastapi/applications.py", "openapi")
    assert boosted > 10.0
    assert demoted < 10.0


def test_build_mirror_groups_tracks_test_and_impl():
    groups = build_mirror_groups(["app/user.py", "spec/user_spec.rb"])
    assert len(groups) == 1
    entry = next(iter(groups.values()))
    assert entry["has_test"] and entry["has_non_test"]


def test_mmr_select_limits_results():
    candidates = [{"path": f"src/f{i}.py", "weight": 10 - i, "_symbols": []} for i in range(5)]
    selected = mmr_select(candidates, limit=2, mmr_lambda=0.7)
    assert len(selected) == 2
