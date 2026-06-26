"""Build profile instrumentation (Phase 10.1)."""

from __future__ import annotations

from pareto_context_graph.build_profile import read_build_profile
from pareto_context_graph.graph import build_graph
from pareto_context_graph.store import Store


def test_build_writes_profile_meta(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=25, seed=11)
    store = build_graph(repo, max_commits=120)
    profile = read_build_profile(store)
    store.close()

    assert profile is not None
    assert profile["total_sec"] > 0
    phases = profile["phases_sec"]
    assert "sqlite_writes" in phases
    assert "top_neighbours" in phases
    assert "search_indexes" in phases
    assert sum(profile["pct"].values()) >= 99.0


def test_profile_build_show_script(synthetic_repo_factory, tmp_path):
    repo = synthetic_repo_factory(commits=60, files=20, seed=12)
    build_graph(repo, max_commits=80)
    store = Store(repo)
    assert read_build_profile(store) is not None
    store.close()
