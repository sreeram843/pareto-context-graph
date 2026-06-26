"""Phase 10.3 incremental build and search-index tests."""

from __future__ import annotations

import subprocess

from pareto_context_graph.graph import (
    _get_commits_for_window,
    _load_cached_commits,
    _window_key,
    build_graph,
    build_graph_sharded,
    incremental_update,
)
from pareto_context_graph.indexing import update_search_indexes


def test_commit_window_cache_round_trip(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=120, files=25, seed=21)
    window_key = _window_key(200, None, 1)
    commits = _get_commits_for_window(
        repo,
        max_commits=200,
        since=None,
        window_key=window_key,
    )
    assert commits
    cached = _load_cached_commits(repo, window_key)
    assert cached is not None
    assert len(cached) == len(commits)


def test_build_noop_when_head_unchanged(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=100, files=20, seed=22)
    first = build_graph(repo, max_commits=150)
    files_before = first.file_count()
    first.close()

    second = build_graph_sharded(repo, max_commits=150, shards=1)
    assert second.get_meta("build_status") == "noop"
    assert second.file_count() == files_before
    second.close()


def test_incremental_search_indexes_skip_unchanged(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=15, seed=23)
    store = build_graph(repo, max_commits=120)
    stats_full = update_search_indexes(store, repo, full=True)
    assert stats_full["indexed"] > 0

    stats_repeat = update_search_indexes(store, repo, paths=set(store.all_files()))
    assert stats_repeat["unchanged"] >= stats_full["indexed"]
    assert stats_repeat["indexed"] == 0
    store.close()


def test_incremental_update_after_new_commit(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=12, seed=24)
    store = build_graph(repo, max_commits=80)
    before_edges = store.edge_count()
    last_hash = store.get_meta("last_commit_hash")
    store.close()

    target = repo / "src" / "a.py"
    target.write_text(target.read_text() + "\n# touch\n")
    subprocess.run(["git", "add", "src/a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "touch a (#9999)"], cwd=repo, check=True)

    updated = incremental_update(repo)
    assert updated.get_meta("last_commit_hash") != last_hash
    assert updated.edge_count() >= before_edges
    updated.close()
