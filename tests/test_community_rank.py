"""Community-aware ranking boost tests."""

from __future__ import annotations

from pareto_context_graph.community import (
    COMMUNITY_RANK_BOOST,
    community_membership_map,
    community_rank_boost,
)
from pareto_context_graph.store import Store


def test_community_rank_boost_same_cluster():
    membership = {
        "pkg/a.go": 0,
        "pkg/b.go": 0,
        "other/x.go": 1,
    }
    assert community_rank_boost("pkg/b.go", ["pkg/a.go"], membership) > 0
    assert community_rank_boost("other/x.go", ["pkg/a.go"], membership) == 0


def test_community_rank_boost_seed_only_scales_cluster_by_locality():
    membership = {
        "pkg/_compat/v2.py": 0,
        "pkg/routing.py": 0,
    }
    seed = ["pkg/_compat/v2.py"]
    assert community_rank_boost("pkg/routing.py", seed, membership) == COMMUNITY_RANK_BOOST
    scaled = community_rank_boost("pkg/routing.py", seed, membership, seed_only=True)
    assert 0 < scaled < COMMUNITY_RANK_BOOST


def test_community_rank_boost_seed_only_same_directory_singleton():
    membership = {
        "fastapi/_compat/v2.py": 0,
        "fastapi/_compat/shared.py": 99,
    }
    assert (
        community_rank_boost(
            "fastapi/_compat/shared.py",
            ["fastapi/_compat/v2.py"],
            membership,
            seed_only=True,
        )
        == COMMUNITY_RANK_BOOST
    )


def test_community_rank_boost_query_mode_unchanged():
    membership = {
        "fastapi/_compat/v2.py": 0,
        "fastapi/routing.py": 0,
    }
    assert (
        community_rank_boost(
            "fastapi/routing.py",
            ["fastapi/_compat/v2.py"],
            membership,
        )
        == COMMUNITY_RANK_BOOST
    )


def test_community_membership_map_connected_components(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    for path in ("a.py", "b.py", "c.py", "x.py"):
        store.upsert_file(path)
    store.record_co_change("a.py", "b.py", weight=5.0)
    store.record_co_change("b.py", "c.py", weight=5.0)
    store.commit()
    mapping = community_membership_map(store, use_leiden=False)
    assert mapping["a.py"] == mapping["b.py"] == mapping["c.py"]
    assert mapping["x.py"] != mapping["a.py"]
    store.close()
