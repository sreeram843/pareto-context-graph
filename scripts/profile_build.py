#!/usr/bin/env python3
"""Profile graph build phases (Phase 10.1).

Reads ``build_profile`` meta from a built graph, or runs a timed build / index replay.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from pareto_context_graph.build_profile import META_KEY, read_build_profile
from pareto_context_graph.graph import build_graph_sharded
from pareto_context_graph.indexing import rebuild_search_indexes
from pareto_context_graph.store import Store


def _print_profile(payload: dict) -> None:
    print(json.dumps(payload, indent=2))
    phases = payload.get("phases_sec", {})
    pct = payload.get("pct", {})
    if phases:
        print("\nPhase breakdown:")
        for name in phases:
            sec = phases[name]
            share = pct.get(name, 0.0)
            print(f"  {name:18s} {sec:8.1f}s  ({share:5.1f}%)")


def show_profile(repo: Path) -> int:
    store = Store(repo)
    try:
        if store.file_count() == 0:
            print(f"No graph at {repo / '.pareto-context-graph'}", file=sys.stderr)
            return 1
        profile = read_build_profile(store)
        if profile is None:
            print(
                "No build_profile meta (graph built before Phase 10.1). "
                "Re-run build or use --replay-index.",
                file=sys.stderr,
            )
            return 1
        _print_profile(profile)
        return 0
    finally:
        store.close()


def replay_index_phases(repo: Path) -> dict:
    """Time post-ingest phases on an existing graph without mutating it."""
    timings: dict[str, float] = {}
    tmp = Path(tempfile.mkdtemp())
    try:
        src = repo / ".pareto-context-graph"
        dst = tmp / ".pareto-context-graph"
        shutil.copytree(src, dst)
        store = Store(tmp)
        started = time.perf_counter()
        store.rebuild_top_neighbours(k=50)
        timings["top_neighbours"] = time.perf_counter() - started

        started = time.perf_counter()
        index_stats = rebuild_search_indexes(store, repo)
        timings["search_indexes"] = time.perf_counter() - started

        started = time.perf_counter()
        store.rebuild_files_fts()
        timings["files_fts"] = time.perf_counter() - started

        store.close()
        file_count = len(list((tmp / ".pareto-context-graph").glob("*.db")))
        total = sum(timings.values())
        return {
            "mode": "replay_index",
            "repo": str(repo),
            "phases_sec": {k: round(v, 3) for k, v in timings.items()},
            "total_sec": round(total, 3),
            "pct": {k: round(100 * v / total, 1) for k, v in timings.items()},
            "search_index_stats": index_stats,
            "graph_db_copied": file_count > 0,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_build(
    repo: Path,
    *,
    commits: int,
    since: str | None,
    shards: int,
) -> dict:
    started = time.perf_counter()
    store = build_graph_sharded(
        repo,
        max_commits=commits,
        since=since,
        shards=shards,
    )
    wall = time.perf_counter() - started
    profile = read_build_profile(store) or {}
    profile["wall_clock_sec"] = round(wall, 3)
    store.close()
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repository root")
    parser.add_argument("--show", action="store_true", help="Print build_profile from graph meta")
    parser.add_argument(
        "--replay-index",
        action="store_true",
        help="Time top_neighbours + search_indexes + FTS on a copy (no git rebuild)",
    )
    parser.add_argument("--commits", type=int, default=5000)
    parser.add_argument("--since", default=None)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument(
        "--build",
        action="store_true",
        help="Run a full instrumented build (destructive: clears .pareto-context-graph)",
    )
    args = parser.parse_args()

    repo = args.repo.resolve()
    if args.show:
        return show_profile(repo)
    if args.replay_index:
        payload = replay_index_phases(repo)
        _print_profile(payload)
        return 0
    if args.build:
        payload = run_build(
            repo,
            commits=args.commits,
            since=args.since,
            shards=args.shards,
        )
        _print_profile(payload)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
