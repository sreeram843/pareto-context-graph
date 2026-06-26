"""Phase 6 synthetic huge-repo stress checks (CI-friendly)."""

from __future__ import annotations

from pareto_context_graph.bench import (
    benchmark_context_latencies,
    benchmark_incremental_update,
    pick_hub_seed,
    run_repo_benchmark,
)
from pareto_context_graph.graph import build_graph
from tests.fixtures.build_repo import create_synthetic_repo


def test_synthetic_huge_profile_stress(tmp_path):
    repo = tmp_path / "stress"
    create_synthetic_repo(repo, commit_count=400, file_count=80, seed=42)
    entry = run_repo_benchmark(
        repo,
        "synthetic",
        build=True,
        profile="huge",
        commits=450,
        since=None,
        shards=2,
        context_rounds=2,
    )

    assert entry["stats"]["files"] > 0
    assert entry["stats"]["edges"] > 0
    assert entry["graph_db_bytes"] > 0
    assert entry["incremental_update"]["seconds"] < 30.0

    p95 = entry["context_latency"]["context"]["p95_seconds"]
    assert p95 < 10.0, f"context p95 too slow: {p95}s"


def test_hub_seed_context_latency(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=300, files=60, seed=9)
    store = build_graph(repo, max_commits=500)
    store.close()

    hub = pick_hub_seed(repo)
    update = benchmark_incremental_update(repo)
    latencies = benchmark_context_latencies(
        repo,
        hub_seed=hub,
        queries=["module handler", "test update"],
        profile="large",
        rounds=2,
    )

    assert hub
    assert update["seconds"] < 30.0
    assert latencies["context"]["samples"] >= 2
    assert latencies["context"]["p95_seconds"] < 10.0
    savings = latencies.get("token_savings") or {}
    assert "mean_reduction_vs_agent" in savings
    assert savings.get("samples")
    assert all("reduction_vs_agent" in row for row in savings["samples"])
