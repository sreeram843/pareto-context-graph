"""Huge-repo stress benchmarks (Phase 6)."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deadlines import DEFAULT_TIMEOUT_MS
from .graph import build_graph_sharded, incremental_update
from .profiles import resolve_profile
from .savings import token_reduction_row
from .server import _handle_tool_call
from .store import DB_DIR, Store

DEFAULT_RESULTS_PATH = Path("tests/eval/bench_results.json")

REPO_BENCH_DEFAULTS: dict[str, dict[str, Any]] = {
    "kubernetes": {
        "profile": "huge",
        "since": "12 months ago",
        "commits": 50_000,
        "shards": 4,
        "queries": [
            "api server handler registration",
            "kubelet pod lifecycle",
            "controller reconcile loop",
        ],
    },
    "linux": {
        "profile": "huge",
        "since": "24 months ago",
        "commits": 100_000,
        "shards": 8,
        "queries": [
            "syscall scheduler",
            "driver probe init",
            "memory cgroup",
        ],
    },
}

DEFAULT_QUERIES = [
    "routing handler change",
    "middleware authentication",
    "model validation error",
]


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def latency_summary(times: list[float]) -> dict[str, float | int]:
    ordered = sorted(times)
    return {
        "samples": len(ordered),
        "p50_seconds": round(_percentile(ordered, 0.50), 4),
        "p95_seconds": round(_percentile(ordered, 0.95), 4),
        "max_seconds": round(max(ordered), 4) if ordered else 0.0,
    }


def repo_defaults(repo_key: str) -> dict[str, Any]:
    return dict(REPO_BENCH_DEFAULTS.get(repo_key, {"profile": "huge"}))


def pick_hub_seed(repo_root: Path) -> str:
    store = Store(repo_root)
    try:
        stats = store.graph_stats()
        hubs = stats.get("top_hubs") or []
        if hubs:
            return str(hubs[0]["path"])
        files = store.all_files()
        if files:
            return files[0]
    finally:
        store.close()
    return "README.md"


def _time_context(
    repo_root: Path,
    *,
    seed_files: list[str],
    query: str,
    profile: str,
    token_budget: int = 50_000,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> tuple[float, dict[str, Any]]:
    start = time.perf_counter()
    raw = _handle_tool_call(
        repo_root,
        "pareto_context_graph",
        {
            "command": "context",
            "files": seed_files,
            "query": query,
            "tier": 1,
            "token_budget": token_budget,
            "profile": profile,
            "timeout_ms": timeout_ms,
            "session_memory": False,
        },
    )
    elapsed = time.perf_counter() - start
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    return elapsed, payload


def benchmark_context_latencies(
    repo_root: Path,
    *,
    hub_seed: str,
    queries: list[str],
    profile: str,
    rounds: int = 3,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> dict[str, Any]:
    times: list[float] = []
    hub_times: list[float] = []
    truncated_samples = 0
    total_samples = 0
    token_rows: list[dict[str, Any]] = []
    for query in queries:
        for _ in range(max(1, rounds)):
            elapsed, payload = _time_context(
                repo_root,
                seed_files=[hub_seed],
                query=query,
                profile=profile,
                timeout_ms=timeout_ms,
            )
            times.append(elapsed)
            total_samples += 1
            if payload.get("truncated"):
                truncated_samples += 1
            graph_tokens = int(payload.get("tokens_used", 0))
            row = token_reduction_row(
                repo_root,
                graph_tokens=graph_tokens,
                query=query,
                seed_files=[hub_seed],
            )
            row["query"] = query
            row["hub_only"] = False
            token_rows.append(row)

            hub_elapsed, hub_payload = _time_context(
                repo_root,
                seed_files=[hub_seed],
                query="",
                profile=profile,
                timeout_ms=timeout_ms,
            )
            hub_times.append(hub_elapsed)
            total_samples += 1
            if hub_payload.get("truncated"):
                truncated_samples += 1
            hub_graph_tokens = int(hub_payload.get("tokens_used", 0))
            hub_row = token_reduction_row(
                repo_root,
                graph_tokens=hub_graph_tokens,
                query="",
                seed_files=[hub_seed],
            )
            hub_row["query"] = ""
            hub_row["hub_only"] = True
            token_rows.append(hub_row)

    reductions = [r["reduction_vs_agent"] for r in token_rows if r["reduction_vs_agent"]]
    mean_reduction = round(sum(reductions) / len(reductions), 2) if reductions else 0.0
    return {
        "hub_seed": hub_seed,
        "timeout_ms": timeout_ms,
        "truncated_samples": truncated_samples,
        "total_samples": total_samples,
        "context": latency_summary(times),
        "hub_only_context": latency_summary(hub_times),
        "token_savings": {
            "samples": token_rows,
            "mean_reduction_vs_agent": mean_reduction,
        },
    }


def benchmark_incremental_update(repo_root: Path) -> dict[str, float | int]:
    start = time.perf_counter()
    store = incremental_update(repo_root)
    store.close()
    elapsed = time.perf_counter() - start
    return {
        "seconds": round(elapsed, 4),
        "noop": elapsed < 30.0,
    }


def graph_db_bytes(repo_root: Path) -> int:
    db_path = repo_root / DB_DIR / "graph.db"
    if not db_path.exists():
        return 0
    return db_path.stat().st_size


def _git_head_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def _tracked_files(repo_root: Path) -> int:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def build_repo_graph(
    repo_root: Path,
    *,
    commits: int,
    since: str | None,
    shards: int,
) -> float:
    start = time.perf_counter()
    store = build_graph_sharded(
        repo_root,
        max_commits=commits,
        since=since,
        shards=shards,
    )
    store.close()
    return time.perf_counter() - start


def collect_stats(repo_root: Path) -> dict[str, Any]:
    store = Store(repo_root)
    try:
        payload = store.graph_stats()
        payload.update(
            {
                "repo": str(repo_root),
                "last_build_commits": store.get_meta("last_build_commits"),
                "total_commits_scanned": store.get_meta("total_commits_scanned"),
                "build_strategy": store.get_meta("build_strategy"),
                "last_build_since": store.get_meta("last_build_since"),
            }
        )
        return payload
    finally:
        store.close()


def merge_bench_results(results_path: Path, entry: dict[str, Any]) -> None:
    data: dict[str, Any] = {"repos": [], "updated_at": entry.get("built_at")}
    if results_path.exists():
        try:
            data = json.loads(results_path.read_text())
        except json.JSONDecodeError:
            pass
    repos = {str(r["repo_key"]): r for r in data.get("repos", [])}
    repos[str(entry["repo_key"])] = entry
    data["repos"] = sorted(repos.values(), key=lambda r: str(r["repo_key"]))
    data["updated_at"] = entry.get("built_at")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(data, indent=2) + "\n")


def run_repo_benchmark(
    repo_root: Path,
    repo_key: str,
    *,
    build: bool = False,
    profile: str | None = None,
    commits: int | None = None,
    since: str | None = None,
    shards: int | None = None,
    context_rounds: int = 3,
    queries: list[str] | None = None,
    skip_incremental: bool = False,
) -> dict[str, Any]:
    """Run post-build stress measurements (and optional build) for one repo."""
    defaults = repo_defaults(repo_key)
    profile_name = profile or str(defaults.get("profile", "huge"))
    profile_cfg = resolve_profile(profile_name)
    commit_limit = (
        commits
        if commits is not None
        else int(defaults.get("commits", profile_cfg.get("commits", 50_000)))
    )
    since_window = since if since is not None else defaults.get("since")
    shard_count = (
        shards if shards is not None else int(defaults.get("shards", profile_cfg.get("shards", 4)))
    )
    query_list = list(queries or defaults.get("queries") or DEFAULT_QUERIES)

    build_seconds: float | None = None
    if build:
        build_seconds = build_repo_graph(
            repo_root,
            commits=commit_limit,
            since=since_window,
            shards=shard_count,
        )

    hub_seed = pick_hub_seed(repo_root)
    context_stats = benchmark_context_latencies(
        repo_root,
        hub_seed=hub_seed,
        queries=query_list,
        profile=profile_name,
        rounds=context_rounds,
    )
    if skip_incremental:
        update_stats = {"seconds": None, "skipped": True}
    else:
        update_stats = benchmark_incremental_update(repo_root)
    stats = collect_stats(repo_root)

    entry: dict[str, Any] = {
        "repo_key": repo_key,
        "path": str(repo_root),
        "sha": _git_head_sha(repo_root),
        "profile": profile_name,
        "build_seconds": round(build_seconds, 2) if build_seconds is not None else None,
        "tracked_files": _tracked_files(repo_root),
        "graph_db_bytes": graph_db_bytes(repo_root),
        "incremental_update": update_stats,
        "context_latency": context_stats,
        "stats": stats,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    return entry
