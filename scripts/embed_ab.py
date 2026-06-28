#!/usr/bin/env python3
"""Phase 4.2 — A/B an embeddings backend on the concept_*/pr_* eval subset.

These are the lowest-recall, no-symbol-anchor cases where semantic embeddings should
help most. This runs the eval twice (embeddings ablated vs active) and reports recall@5
on just that subset, so you can decide whether a configured backend earns its keep.

The default backend is the dependency-free hash backend (PCG_EMBED_BACKEND=noop), which
is NOT semantic — expect ~0 delta there. Point it at a real backend to measure lift:

    PCG_EMBED_BACKEND=ollama  python3 scripts/embed_ab.py --repo-map fastapi=bench/fastapi
    # or, with the optional extra installed:  pip install 'pareto-context-graph[embeddings]'
    PCG_EMBED_BACKEND=local   python3 scripts/embed_ab.py --repo-map fastapi=bench/fastapi

The build must have been produced with the SAME PCG_EMBED_BACKEND (the index records it).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

SUBSET_PREFIXES = ("fastapi_concept", "httpx_concept", "fastapi_pr", "httpx_pr", "fastapi_merge")


def _run_eval(repo_maps: list[str], ablate_embed: bool) -> list[dict]:
    import json

    env = dict(os.environ)
    env["PCG_EDGE_DECAY"] = "0"
    env["PCG_ABLATE_EMBED"] = "1" if ablate_embed else "0"
    cmd = [sys.executable, "-m", "pareto_context_graph", "eval", "--json"]
    for rm in repo_maps:
        cmd += ["--repo-map", rm]
    out = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(out.stderr)
        raise SystemExit(out.returncode)
    return json.loads(out.stdout).get("results", [])


def _subset_recall(rows: list[dict]) -> tuple[float, int]:
    hits = [
        float(r.get("recall_at_5", 0.0))
        for r in rows
        if str(r.get("case_id", "")).startswith(SUBSET_PREFIXES)
    ]
    return (sum(hits) / len(hits) if hits else 0.0), len(hits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-map", action="append", dest="repo_maps", required=True,
                    help="KEY=path (repeatable)")
    args = ap.parse_args()

    backend = os.environ.get("PCG_EMBED_BACKEND", "noop")
    off, n = _subset_recall(_run_eval(args.repo_maps, ablate_embed=True))
    on, _ = _subset_recall(_run_eval(args.repo_maps, ablate_embed=False))
    print(f"embeddings backend: {backend}")
    print(f"concept/pr subset cases: {n}")
    print(f"recall@5  embed OFF: {off:.4f}")
    print(f"recall@5  embed ON : {on:.4f}")
    print(f"delta (on - off)   : {on - off:+.4f}")
    if backend in ("", "noop") and abs(on - off) < 1e-9:
        print("note: noop backend is not semantic — set PCG_EMBED_BACKEND to a real backend to measure lift.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
