#!/usr/bin/env python3
"""Micro-benchmark the cold-build write path (Phase 2.2/2.1).

Isolates SQLite write amplification — the dominant cost of large cold builds — from
git/parsing overhead by inserting N synthetic co-change edges two ways:

  legacy: indexes live + WAL + synchronous=NORMAL during the bulk insert
  fast:   enter_cold_bulk_load() (journal OFF, synchronous=0, idx_co_a/idx_co_b
          dropped), insert, then exit_cold_bulk_load() (recreate indexes, restore WAL)

Also times the Python top-neighbour rebuild (Phase 2.1). Prints a small table.

Usage: python3 scripts/build_microbench.py [--edges 1000000] [--files 1500]
"""

from __future__ import annotations

import argparse
import random
import shutil
import tempfile
import time
from pathlib import Path

from pareto_context_graph.store import Store


def _make_edges(num_edges: int, num_files: int, seed: int = 7):
    random.seed(seed)
    files = [f"src/f{i}.py" for i in range(num_files)]
    edges = []
    seen = set()
    while len(edges) < num_edges:
        a, b = random.randrange(num_files), random.randrange(num_files)
        if a == b:
            continue
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        edges.append((files[a], files[b], 1.0, 1_700_000_000))
    return edges


def _run(mode: str, edges, workdir: Path):
    d = workdir / f"micro_{mode}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    st = Store(d)
    t = time.time()
    if mode == "fast":
        st.enter_cold_bulk_load()
        st.record_co_changes_bulk(edges)
        st.exit_cold_bulk_load()
    else:
        st.record_co_changes_bulk(edges)
        st.commit()
    insert_t = time.time() - t
    t = time.time()
    st.rebuild_top_neighbours(k=50)
    nbr_t = time.time() - t
    ec = st.edge_count()
    st.close()
    return insert_t, nbr_t, ec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", type=int, default=1_000_000)
    ap.add_argument("--files", type=int, default=1500)
    args = ap.parse_args()

    edges = _make_edges(args.edges, args.files)
    print(f"prepared {len(edges):,} edges over {args.files} files\n")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        results = {mode: _run(mode, edges, work) for mode in ("legacy", "fast")}
    legacy_ins = results["legacy"][0]
    fast_ins = results["fast"][0]
    print(f"{'mode':7s} {'insert':>9s} {'top_nbr':>9s} {'total':>9s} {'edges':>12s}")
    for mode, (ins, nbr, ec) in results.items():
        print(f"{mode:7s} {ins:8.2f}s {nbr:8.2f}s {ins + nbr:8.2f}s {ec:>12,}")
    if fast_ins > 0:
        print(f"\ncold-bulk-load insert speedup: {legacy_ins / fast_ins:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
