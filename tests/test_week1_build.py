"""Week 1 build optimizations: neighbours, exclusions, profiles, cold bulk load."""

from __future__ import annotations

from pareto_context_graph.neighbour_cache import compute_top_neighbours_from_merged
from pareto_context_graph.profiles import PROFILES, resolve_profile
from pareto_context_graph.repo_config import load_repo_config, path_excluded
from pareto_context_graph.store import Store


def test_compute_top_neighbours_from_merged_ordering():
    merged = {
        ("a.py", "b.py"): (5.0, 1),
        ("a.py", "c.py"): (3.0, 1),
        ("a.py", "d.py"): (1.0, 1),
    }
    ranked = compute_top_neighbours_from_merged(merged, k=2)
    assert ranked["a.py"][0] == ("b.py", 5.0)
    assert ranked["a.py"][1] == ("c.py", 3.0)


def test_python_top_neighbour_cache_matches_store(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    try:
        store.record_co_change("a.py", "b.py", weight=5.0)
        store.record_co_change("a.py", "c.py", weight=3.0)
        store.record_co_change("a.py", "d.py", weight=1.0)
        store.commit()
        store.rebuild_top_neighbours(k=2)

        top = store.top_neighbours("a.py", limit=5)
        assert len(top) == 2
        assert top[0][0] == "b.py"
        assert top[1][0] == "c.py"
    finally:
        store.close()


def test_path_excluded_defaults(tmp_path):
    cfg = load_repo_config(tmp_path)
    assert path_excluded("node_modules/pkg/index.js", cfg)
    assert path_excluded("vendor/lib/foo.py", cfg)
    assert not path_excluded("src/main.py", cfg)


def test_huge_profile_commit_cap():
    huge = resolve_profile("huge")
    assert huge["commits"] == 20_000
    assert huge["shards"] == 8


def test_huge_full_profile_for_bench():
    full = resolve_profile("huge-full")
    assert full["commits"] == 100_000
    assert full["since"] == "24 months ago"
    assert PROFILES["huge-full"]["shards"] == 8


def test_cold_bulk_load_restores_wal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    try:
        store.enter_cold_bulk_load()
        store.record_co_change("x.py", "y.py", weight=1.0)
        store.exit_cold_bulk_load()
        mode = store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        indexes = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_co_%'"
            ).fetchall()
        }
        assert indexes == {"idx_co_a", "idx_co_b"}
    finally:
        store.close()


def test_filter_paths_drops_vendor(tmp_path):
    from pareto_context_graph.repo_config import filter_paths

    cfg = load_repo_config(tmp_path)
    out = filter_paths(["src/a.py", "vendor/lib.py", "node_modules/x.js"], tmp_path, cfg)
    assert out == ["src/a.py"]
