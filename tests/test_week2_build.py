"""Week 2: phased build, batched index commits, shards=1 pre-aggregation."""

from __future__ import annotations

from pareto_context_graph.graph import build_graph_sharded
from pareto_context_graph.indexing import (
    SEARCH_INDEX_STATUS_META,
    ensure_search_indexes,
)
from pareto_context_graph.profiles import resolve_profile
from pareto_context_graph.repo_config import resolve_search_index_mode
from pareto_context_graph.store import Store


def test_huge_profile_lazy_search_index_by_default(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert resolve_search_index_mode(repo, profile_name="huge") == "lazy"
    assert resolve_search_index_mode(repo, profile_name="huge-full") == "lazy"
    assert resolve_search_index_mode(repo, profile_name="tiny") == "eager"


def test_huge_max_files_per_commit():
    huge = resolve_profile("huge")
    assert huge["max_files_per_commit"] == 50


def test_lazy_build_defers_search_index(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=12, seed=31)
    store = build_graph_sharded(
        repo,
        max_commits=120,
        shards=1,
        profile_name="huge",
    )
    try:
        assert store.get_meta(SEARCH_INDEX_STATUS_META) == "pending"
        profile = store.get_meta("build_profile")
        assert profile is not None
    finally:
        store.close()


def test_eager_build_completes_search_index(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=12, seed=32)
    store = build_graph_sharded(
        repo,
        max_commits=120,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    try:
        assert store.get_meta(SEARCH_INDEX_STATUS_META) == "complete"
    finally:
        store.close()


def test_ensure_search_indexes_completes_lazy_build(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=12, seed=33)
    store = build_graph_sharded(
        repo,
        max_commits=120,
        shards=1,
        profile_name="huge",
    )
    store.close()

    store = Store(repo)
    try:
        assert store.get_meta(SEARCH_INDEX_STATUS_META) == "pending"
        stats = ensure_search_indexes(store, repo, profile_name="huge")
        assert store.get_meta(SEARCH_INDEX_STATUS_META) == "complete"
        assert stats["indexed"] >= 1
    finally:
        store.close()


def test_sharded_single_uses_aggregated_strategy(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=100, files=15, seed=34)
    store = build_graph_sharded(repo, max_commits=150, shards=1, profile_name="tiny")
    try:
        assert store.get_meta("build_strategy") == "sharded-v1:1"
        profile_json = store.get_meta("build_profile")
        assert profile_json is not None
        assert "aggregated-v1" in profile_json or "pair_aggregate" in profile_json
    finally:
        store.close()
