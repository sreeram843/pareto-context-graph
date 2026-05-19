"""Minimal MCP server exposing code-graph-mcp via stdio.

Single unified tool (`code_graph`) to minimize schema token overhead.
Zero-dependency MCP implementation using JSON-RPC 2.0 over stdin/stdout.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from .blast import (
    blast_radius,
    classify_query_intent,
    extract_dbt_refs,
    extract_imports,
    file_class,
    find_directory_siblings,
    filter_existing,
    find_naming_pairs,
    get_file_summary,
    resolve_dbt_ref_to_file,
    resolve_import_to_file,
    surprise_score,
)
from .chunks import (
    KeywordIndex,
    extract_symbols,
    get_relevant_chunks,
    get_signatures,
)
from .embed import query_embedding_scores
from .graph import build_graph, get_changed_files, incremental_update
from .hooks import (
    redact_context_payload,
    run_post_build_hooks,
    run_post_context_hooks,
    run_post_update_hooks,
    run_pre_context_hooks,
)
from .profiles import autodetect_profile, resolve_profile
from .store import Store
from .tokens import BYTES_PER_TOKEN, compute_savings, estimate_file_tokens, estimate_tokens
from .walk import random_walk_with_restart

# Single tool — reduces schema overhead from ~1000 tokens to ~200 tokens
TOOLS = [
    {
        "name": "code_graph",
        "description": "Git-aware code intelligence. Commands: context (get related files for any prompt), build (build graph), update (incremental update), blast (files affected by diff), neighbours (co-change lookup), search (find files), stats, doctor, hotspots, communities, decay_sweep, mark_used.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "One of: context, build, update, blast, neighbours, search, stats, doctor, hotspots, communities, savings, decay_sweep, mark_used",
                    "enum": ["context", "build", "update", "blast", "neighbours", "search", "stats", "doctor", "hotspots", "communities", "savings", "decay_sweep", "mark_used"],
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "[context] File paths you're working in or asking about",
                },
                "query": {
                    "type": "string",
                    "description": "[context/search] Your question/task, or search term",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "[context] Max tokens (default: 50000)",
                    "default": 50000,
                },
                "tier": {
                    "type": "integer",
                    "description": "[context] Detail level: 1=summaries (default), 2=signatures, 3=chunks/content",
                    "default": 1,
                    "enum": [1, 2, 3],
                },
                "already_have": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "[context] Files you already have in context (won't be resent)",
                },
                "base": {
                    "type": "string",
                    "description": "[blast/savings] Base branch (default: main)",
                    "default": "main",
                },
                "path": {
                    "type": "string",
                    "description": "[neighbours] File path to query",
                },
                "max_commits": {
                    "type": "integer",
                    "description": "[build] Max commits to analyze (default: 5000)",
                    "default": 5000,
                },
                "since": {
                    "type": "string",
                    "description": "[build] Optional git --since expression (e.g. \"12 months ago\" or \"2025-01-01\")",
                },
                "shards": {
                    "type": "integer",
                    "description": "[build] Number of parallel shard workers",
                },
                "profile": {
                    "type": "string",
                    "description": "Preset tuning profile: tiny, medium, large, huge",
                },
                "min_weight": {
                    "type": "integer",
                    "description": "Min co-change count (default: 2)",
                    "default": 2,
                },
                "max_depth": {
                    "type": "integer",
                    "description": "BFS hops (default: 2)",
                    "default": 2,
                },
                "top_n": {
                    "type": "integer",
                    "description": "[hotspots] Number to return (default: 10)",
                    "default": 10,
                },
                "limit": {
                    "type": "integer",
                    "description": "[search] Max results (default: 20)",
                    "default": 20,
                },
                "half_life_days": {
                    "type": "number",
                    "description": "[decay_sweep] Exponential decay half-life in days",
                },
                "prune_below": {
                    "type": "number",
                    "description": "[decay_sweep] Delete edges with weight below threshold",
                },
                "hub_penalty_strength": {
                    "type": "number",
                    "description": "[context] Strength of hub suppression (default 1.0)",
                },
                "iterations": {
                    "type": "integer",
                    "description": "[context] Iterative retrieval rounds",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "[mark_used] Context file paths that user actually used",
                },
                "no_safety": {
                    "type": "boolean",
                    "description": "[context] Disable secret redaction on returned content",
                },
            },
            "required": ["command"],
        },
    },
]

# In-memory keyword index (built lazily on first context call)
_keyword_index: KeywordIndex | None = None
_keyword_index_repo: Path | None = None
_node_degree_cache: dict[str, int] | None = None
_node_degree_repo: Path | None = None
_learned_weights_cache: dict[str, float] | None = None
_learned_weights_repo: Path | None = None


def invalidate_caches() -> None:
    """Reset all in-process caches (call after a graph build or update)."""
    global _keyword_index, _node_degree_cache, _learned_weights_cache
    _keyword_index = None
    _node_degree_cache = None
    _learned_weights_cache = None

# Test-file detection — used to deprioritise specs when query isn't test-focused
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:spec|test|tests|__tests__)/"
    r"|_(?:spec|test)\."
    r"|\.(?:spec|test)\.",
    re.IGNORECASE,
)
_TEST_QUERY_TERMS = frozenset({
    "test", "spec", "mock", "stub", "fixture", "factory",
    "rspec", "jest", "pytest", "minitest", "coverage", "assert",
})


def _is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _query_is_test_focused(query: str) -> bool:
    return any(t in _TEST_QUERY_TERMS for t in query.lower().split())


def _mirror_key(path: str) -> tuple[str, str]:
    """Normalize impl/spec variants into the same ranking bucket."""
    pure = Path(path)
    parent = str(pure.parent).replace("/__tests__", "").replace("/tests", "").replace("/test", "").replace("/spec", "")
    stem = pure.stem.lower()
    for suffix in ("_spec", "_test", ".spec", ".test"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    if stem.startswith("test_"):
        stem = stem[5:]
    return parent, stem


def _build_mirror_groups(paths: list[str]) -> dict[str, dict[str, bool]]:
    """Track whether a normalized file family has test and/or non-test members."""
    groups: dict[str, dict[str, bool]] = {}
    for path in paths:
        key = "::".join(_mirror_key(path))
        entry = groups.setdefault(key, {"has_test": False, "has_non_test": False})
        if _is_test_file(path):
            entry["has_test"] = True
        else:
            entry["has_non_test"] = True
    return groups


def _apply_file_class_weight(base_score: float, path: str, query_intent: str) -> float:
    """Apply lightweight path-class weighting for the current query intent."""
    kind = file_class(path)
    path_lower = path.lower()

    if kind == "generated":
        return base_score * 0.01
    if kind == "fixture":
        return base_score * 0.1

    if query_intent == "endpoint":
        if kind == "route":
            return base_score * 2.5
        if kind == "source" and any(token in path_lower for token in ("controller", "model", "service", "serializer", "patient")):
            return base_score * 1.4
        if kind == "doc":
            return base_score * 0.2
    elif query_intent == "schema":
        if kind == "migration":
            return base_score * 2.0
        if kind == "route":
            return base_score * 0.5
    elif query_intent == "docs":
        if kind == "doc":
            return base_score * 2.0
    elif query_intent == "test":
        if kind == "test":
            return base_score * 2.0

    if kind == "doc":
        return base_score * 0.5
    return base_score


def _all_repo_files(repo_root: Path) -> set[str]:
    """List repository files for cold-start fallback when the graph is sparse."""
    paths: set[str] = set()
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if rel.startswith(".git/") or rel.startswith(".code-graph/") or rel.startswith(".venv/"):
            continue
        paths.add(rel)
    return paths


def _shared_path_depth(candidate: str, seed_files: list[str]) -> int:
    """Return max number of shared directory components between candidate and any seed."""
    cand_parts = Path(candidate).parent.parts
    best = 0
    for seed in seed_files:
        seed_parts = Path(seed).parent.parts
        shared = sum(1 for _ in zip(cand_parts, seed_parts) if _ == (_[0], _[0]))
        # zip gives (a,b) pairs; we need to count leading matches
        shared = 0
        for a, b in zip(cand_parts, seed_parts):
            if a == b:
                shared += 1
            else:
                break
        best = max(best, shared)
    return best


def _locality_multiplier(candidate: str, seed_files: list[str]) -> float:
    """Score multiplier based on directory proximity to the nearest seed file.

    Same directory      → 3.0×
    One level up        → 1.8×
    Two levels up       → 1.3×
    Deeper mismatch     → 1.0× (no change)
    """
    if not seed_files:
        return 1.0
    depth = _shared_path_depth(candidate, seed_files)
    if depth == 0:
        return 1.0
    seed_depths = [len(Path(s).parent.parts) for s in seed_files]
    max_seed_depth = max(seed_depths) if seed_depths else 1
    gap = max(0, max_seed_depth - depth)
    if gap == 0:
        return 3.0
    if gap == 1:
        return 1.8
    if gap == 2:
        return 1.3
    return 1.0


def _build_keyword_index(repo_root: Path, store: Store) -> KeywordIndex:
    """Build or return cached keyword index for semantic matching."""
    global _keyword_index, _keyword_index_repo
    if _keyword_index is not None and _keyword_index_repo == repo_root:
        return _keyword_index

    idx = KeywordIndex()
    for file_path in store.all_files():
        fp = repo_root / file_path
        if fp.is_file() and fp.stat().st_size < 100000:  # Skip huge files
            try:
                content = fp.read_text(errors="ignore")[:20000]
                idx.index_file(file_path, content)
            except OSError:
                pass

    _keyword_index = idx
    _keyword_index_repo = repo_root
    return idx


def _node_degrees(repo_root: Path, store: Store) -> dict[str, int]:
    global _node_degree_cache, _node_degree_repo
    if _node_degree_cache is not None and _node_degree_repo == repo_root:
        return _node_degree_cache
    _node_degree_cache = store.node_degrees()
    _node_degree_repo = repo_root
    return _node_degree_cache


def _learned_weights(repo_root: Path) -> dict[str, float]:
    global _learned_weights_cache, _learned_weights_repo
    if _learned_weights_cache is not None and _learned_weights_repo == repo_root:
        return _learned_weights_cache

    weights_path = repo_root / ".code-graph" / "weights.json"
    if not weights_path.exists():
        _learned_weights_cache = {}
        _learned_weights_repo = repo_root
        return _learned_weights_cache

    try:
        payload = json.loads(weights_path.read_text())
    except Exception:
        payload = {}
    _learned_weights_cache = {str(k): float(v) for k, v in payload.items()}
    _learned_weights_repo = repo_root
    return _learned_weights_cache


def _stage1_candidates(
    store: Store,
    seed_files: list[str],
    *,
    min_weight: int,
    max_depth: int,
    cap: int,
    expansion: str,
) -> list[dict]:
    if expansion == "rwr":
        walk_scores = random_walk_with_restart(
            store,
            seed_files,
            walks=200,
            length=max_depth + 4,
            restart=0.15,
        )
        ranked = sorted(walk_scores.items(), key=lambda item: item[1], reverse=True)
        out: list[dict] = []
        for path, score in ranked:
            if path in seed_files:
                continue
            out.append({"path": path, "depth": 1, "weight": max(1, int(score * 1000)), "signal": "rwr"})
            if len(out) >= cap:
                break
        return out

    results = blast_radius(store, seed_files, min_weight=min_weight, max_depth=max_depth, use_cache=True)
    filtered = [r for r in results if r.get("path") not in set(seed_files)]
    return filtered[:cap]


def _dir_tokens(path: str) -> set[str]:
    return {part for part in Path(path).parts if part}


def _similarity_for_mmr(path_a: str, symbols_a: list[str], path_b: str, symbols_b: list[str]) -> float:
    a_tokens = _dir_tokens(path_a)
    b_tokens = _dir_tokens(path_b)
    union = len(a_tokens | b_tokens) or 1
    path_jaccard = len(a_tokens & b_tokens) / union
    sym_a = set(symbols_a)
    sym_b = set(symbols_b)
    sym_union = len(sym_a | sym_b) or 1
    sym_jaccard = len(sym_a & sym_b) / sym_union
    return 0.7 * path_jaccard + 0.3 * sym_jaccard


def _mmr_select(candidates: list[dict], limit: int, mmr_lambda: float) -> list[dict]:
    if not candidates:
        return []
    remaining = candidates[:]
    selected: list[dict] = []

    while remaining and len(selected) < limit:
        best_idx = 0
        best_score = float("-inf")
        for idx, cand in enumerate(remaining):
            relevance = float(cand.get("_relevance", cand.get("weight", 0)))
            if not selected:
                mmr = relevance
            else:
                max_sim = 0.0
                for chosen in selected:
                    sim = _similarity_for_mmr(
                        cand["path"],
                        cand.get("_symbols", []),
                        chosen["path"],
                        chosen.get("_symbols", []),
                    )
                    max_sim = max(max_sim, sim)
                mmr = (mmr_lambda * relevance) - ((1.0 - mmr_lambda) * max_sim)
            if mmr > best_score:
                best_score = mmr
                best_idx = idx
        selected.append(remaining.pop(best_idx))

    return selected


def _handle_tool_call(repo_root: Path, name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""

    # Support both old multi-tool names and new unified tool
    if name == "code_graph":
        command = arguments.get("command", "")
    else:
        # Backwards compatibility: map old tool names to commands
        _name_map = {
            "build_graph": "build", "update_graph": "update",
            "get_blast_radius": "blast", "get_token_savings": "savings",
            "get_neighbours": "neighbours", "get_graph_stats": "stats",
            "get_hotspots": "hotspots", "search_graph": "search",
            "get_communities": "communities", "get_context": "context",
        }
        command = _name_map.get(name, "")
        if not command:
            return json.dumps({"error": f"Unknown tool: {name}"})

    # --- build ---
    if command == "build":
        from .graph import build_graph_sharded

        profile_name = arguments.get("profile") or autodetect_profile(repo_root)
        profile = resolve_profile(profile_name)
        max_commits = arguments.get("max_commits", profile.get("commits", 5000))
        since = arguments.get("since")
        if since is None:
            since = profile.get("since")
        shards = int(arguments.get("shards", profile.get("shards", 1)))
        store = build_graph_sharded(
            repo_root,
            max_commits=max_commits,
            since=since,
            shards=shards,
        )
        result = {
            "status": "ok",
            "files": store.file_count(),
            "edges": store.edge_count(),
            "profile": profile_name,
            "shards": shards,
        }
        if since:
            result["since"] = since
        store.close()
        # Invalidate keyword index cache
        global _keyword_index, _node_degree_cache, _learned_weights_cache
        _keyword_index = None
        _node_degree_cache = None
        _learned_weights_cache = None
        result = run_post_build_hooks(repo_root, result)
        return json.dumps(result)

    # --- update ---
    if command == "update":
        store = incremental_update(repo_root)
        result = {
            "status": "ok",
            "files": store.file_count(),
            "edges": store.edge_count(),
        }
        store.close()
        _keyword_index = None
        _node_degree_cache = None
        _learned_weights_cache = None
        result = run_post_update_hooks(repo_root, result)
        return json.dumps(result)

    # --- decay_sweep ---
    if command == "decay_sweep":
        from .graph import decay_sweep

        profile_name = arguments.get("profile") or autodetect_profile(repo_root)
        profile = resolve_profile(profile_name)
        half_life_days = float(arguments.get("half_life_days", profile.get("half_life_days", 180)))
        prune_below = arguments.get("prune_below", profile.get("prune_below"))

        store = decay_sweep(
            repo_root,
            half_life_days=half_life_days,
            prune_below=prune_below,
        )
        result = {
            "status": "ok",
            "files": store.file_count(),
            "edges": store.edge_count(),
            "half_life_days": half_life_days,
            "prune_below": prune_below,
        }
        store.close()
        _keyword_index = None
        _node_degree_cache = None
        _learned_weights_cache = None
        return json.dumps(result)

    # --- blast ---
    if command == "blast":
        base = arguments.get("base", "main")
        min_weight = arguments.get("min_weight", 2)
        max_depth = arguments.get("max_depth", 2)

        store = Store(repo_root)
        changed = get_changed_files(repo_root, base=base)
        results = blast_radius(
            store, changed, min_weight=min_weight, max_depth=max_depth
        )
        existing = filter_existing(repo_root, [r["path"] for r in results])
        filtered = [r for r in results if r["path"] in existing]
        store.close()

        return json.dumps({
            "changed": changed,
            "affected": [r["path"] for r in filtered if r["depth"] > 0],
            "total": len(existing),
        })

    # --- savings ---
    if command == "savings":
        base = arguments.get("base", "main")
        store = Store(repo_root)
        changed = get_changed_files(repo_root, base=base)
        results = blast_radius(store, changed)
        existing = filter_existing(repo_root, [r["path"] for r in results])
        savings = compute_savings(repo_root, existing)
        store.close()
        return json.dumps({
            "blast_tokens": savings["blast_tokens"],
            "saved_tokens": savings["saved_tokens"],
            "reduction": f"{savings['percent_reduction']}%",
            "efficiency": f"{savings['multiplier']}x",
        })

    # --- neighbours ---
    if command == "neighbours":
        path = arguments.get("path", "")
        min_weight = arguments.get("min_weight", 2)
        store = Store(repo_root)
        neighbours = store.neighbours(path, min_weight=min_weight)
        store.close()
        return json.dumps([{"path": p, "weight": w} for p, w in neighbours])

    # --- stats ---
    if command == "stats":
        store = Store(repo_root)
        result = store.graph_stats()
        result.update({
            "last_build_commits": store.get_meta("last_build_commits"),
            "total_commits_scanned": store.get_meta("total_commits_scanned"),
            "build_strategy": store.get_meta("build_strategy"),
            "last_build_since": store.get_meta("last_build_since"),
        })
        store.close()
        return json.dumps(result)

    # --- doctor ---
    if command == "doctor":
        store = Store(repo_root)
        result = store.graph_stats()
        result.update({
            "last_build_commits": store.get_meta("last_build_commits"),
            "total_commits_scanned": store.get_meta("total_commits_scanned"),
            "build_strategy": store.get_meta("build_strategy"),
            "last_build_since": store.get_meta("last_build_since"),
            "last_commit_hash": store.get_meta("last_commit_hash"),
        })
        store.close()
        return json.dumps(result)

    # --- mark_used ---
    if command == "mark_used":
        paths = arguments.get("paths", [])
        store = Store(repo_root)
        updated = store.mark_feedback_used(paths)
        store.close()
        return json.dumps({"updated": updated})

    # --- hotspots ---
    if command == "hotspots":
        top_n = arguments.get("top_n", 10)
        store = Store(repo_root)
        hotspots = store.get_hotspots(top_n=top_n)
        store.close()
        return json.dumps(hotspots)

    # --- search ---
    if command == "search":
        query = arguments.get("query", "")
        limit = arguments.get("limit", 20)
        store = Store(repo_root)
        results = store.search_files(query, limit=limit)
        store.close()
        return json.dumps({"files": results, "count": len(results)})

    # --- communities ---
    if command == "communities":
        min_weight = arguments.get("min_weight", 3)
        store = Store(repo_root)
        communities = store.get_communities(min_weight=min_weight)
        store.close()
        top = communities[:10]
        return json.dumps({
            "communities": [{"files": c, "size": len(c)} for c in top],
            "total_communities": len(communities),
        })

    # --- context (primary tool — 6-phase pipeline with tiers) ---
    if command == "context":
        arguments = run_pre_context_hooks(repo_root, arguments)
        files = arguments.get("files", [])
        if not files:
            return json.dumps({"error": "files parameter is required"})

        profile_name = arguments.get("profile") or autodetect_profile(repo_root)
        profile = resolve_profile(profile_name)
        query = arguments.get("query", "")
        token_budget = arguments.get("token_budget", 50000)
        tier = arguments.get("tier", 1)
        already_have = set(arguments.get("already_have", []))
        min_weight = arguments.get("min_weight", profile.get("min_weight", 2))
        max_depth = arguments.get("max_depth", profile.get("max_depth", 2))
        stage1_cap = int(arguments.get("stage1_cap", profile.get("stage1_cap", 500)))
        expansion = arguments.get("expansion", profile.get("expansion", "bfs"))
        iterations = int(arguments.get("iterations", profile.get("iterations", 1)))
        hub_penalty_strength = float(arguments.get("hub_penalty_strength", profile.get("hub_penalty_strength", 1.0)))
        mmr_lambda = float(arguments.get("mmr_lambda", profile.get("mmr_lambda", 0.7)))
        no_safety = bool(arguments.get("no_safety", False))

        store = Store(repo_root)
        sparse_graph = store.edge_count() < 500

        # --- Phase 1: candidate generation (BFS/RWR, optionally iterative) ---
        stage1_results: list[dict] = []
        seed_pool = list(files)
        for _round in range(max(1, iterations)):
            generated = _stage1_candidates(
                store,
                seed_pool,
                min_weight=min_weight,
                max_depth=max_depth,
                cap=stage1_cap,
                expansion=expansion,
            )
            existing_paths = {item["path"] for item in stage1_results}
            additions = [g for g in generated if g["path"] not in existing_paths]
            stage1_results.extend(additions)
            if not additions:
                break
            seed_pool = [a["path"] for a in additions[: min(25, len(additions))]]

        results = stage1_results

        # --- Phase 2: Hybrid signals ---
        all_files_set = set(store.all_files())
        if sparse_graph:
            all_files_set |= _all_repo_files(repo_root)
        hybrid_additions: list[dict] = []

        for seed in files:
            seed_path = repo_root / seed
            if seed_path.is_file():
                imports = extract_imports(seed_path)
                for ref in imports:
                    resolved = resolve_import_to_file(ref, all_files_set, from_path=seed)
                    if resolved and resolved not in {r["path"] for r in results}:
                        hybrid_additions.append({
                            "path": resolved,
                            "depth": 1,
                            "weight": 1,
                            "source": seed,
                            "signal": "import",
                        })

                # dbt {{ ref() }} and {{ source() }} resolution (SQL/Jinja files)
                if seed_path.suffix in {".sql", ".jinja", ".jinja2", ".j2"}:
                    dbt_refs = extract_dbt_refs(seed_path)
                    for ref_name in dbt_refs:
                        resolved = resolve_dbt_ref_to_file(ref_name, all_files_set)
                        if resolved and resolved not in {r["path"] for r in results}:
                            hybrid_additions.append({
                                "path": resolved,
                                "depth": 1,
                                "weight": 3,
                                "source": seed,
                                "signal": "dbt_ref",
                            })

                # Also check which files in the graph reference this seed via dbt ref
                seed_stem = seed_path.stem.lower()
                # Models referencing the seed get added too (reverse lookup — lightweight)
                for candidate in list(all_files_set):
                    if candidate == seed or not candidate.endswith(".sql"):
                        continue
                    cand_path = repo_root / candidate
                    if not cand_path.is_file():
                        continue
                    try:
                        snippet = cand_path.read_text(errors="ignore")[:5000]
                    except OSError:
                        continue
                    if f"ref('{seed_path.stem}')" in snippet or f'ref("{seed_path.stem}")' in snippet:
                        if candidate not in {r["path"] for r in results}:
                            hybrid_additions.append({
                                "path": candidate,
                                "depth": 1,
                                "weight": 3,
                                "source": seed,
                                "signal": "dbt_ref_reverse",
                            })

            pairs = find_naming_pairs(seed, all_files_set)
            for p in pairs:
                if p not in {r["path"] for r in results}:
                    hybrid_additions.append({
                        "path": p,
                        "depth": 1,
                        "weight": 1,
                        "source": seed,
                        "signal": "naming",
                    })

            # Always inject directory siblings (not just for sparse graphs) —
            # local files are almost always relevant even in well-indexed repos.
            siblings = find_directory_siblings(seed, all_files_set)
            for sibling in siblings:
                if sibling not in {r["path"] for r in results}:
                    sibling_weight = 2 if sparse_graph else 1
                    hybrid_additions.append({
                        "path": sibling,
                        "depth": 1,
                        "weight": sibling_weight,
                        "source": seed,
                        "signal": "directory",
                    })

        # Phase 2b: Semantic keyword matching (TF-IDF)
        if query:
            kw_index = _build_keyword_index(repo_root, store)
            kw_results = kw_index.query(query, top_n=15)
            existing_paths = {r["path"] for r in results} | {h["path"] for h in hybrid_additions}
            for kw_path, kw_score in kw_results:
                if kw_path not in existing_paths and kw_path not in files:
                    hybrid_additions.append({
                        "path": kw_path,
                        "depth": 2,
                        "weight": max(1, int(kw_score / 10)),
                        "source": "keyword_match",
                        "signal": "semantic",
                    })

        # Merge hybrid results
        seen = {r["path"] for r in results}
        for h in hybrid_additions:
            if h["path"] not in seen:
                results.append(h)
                seen.add(h["path"])

        # --- Phase 3: Filter existing + delta context ---
        existing = filter_existing(repo_root, [r["path"] for r in results])
        existing_set = set(existing)
        filtered = [
            r for r in results
            if r["path"] in existing_set
            and r["depth"] > 0
            and r["path"] not in already_have
        ]

        # --- Phase 4: Query-aware ranking ---
        test_focused = _query_is_test_focused(query) if query else False
        query_intent = classify_query_intent(query) if query else "default"
        node_degrees = _node_degrees(repo_root, store)
        learned = _learned_weights(repo_root)
        embed_scores = query_embedding_scores(repo_root, query, [r["path"] for r in filtered]) if query else {}
        mirror_groups = _build_mirror_groups([r["path"] for r in filtered])
        if query:
            query_lower = query.lower()
            query_terms = set(query_lower.split())
            for r in filtered:
                path_lower = r["path"].lower()
                term_hits = sum(1 for t in query_terms if t in path_lower)
                base_score = r.get("weight", 1) + (term_hits * 10)
                # Deprioritise spec/test files unless the query is about testing.
                # Specs co-change with every implementation file, so without this
                # penalty they flood the top results.
                mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                if not test_focused and _is_test_file(r["path"]):
                    base_score *= 0.05
                    if mirror_group.get("has_non_test"):
                        base_score *= 0.2
                elif mirror_group.get("has_test"):
                    base_score += 5
                base_score = _apply_file_class_weight(base_score, r["path"], query_intent)
                base_score *= _locality_multiplier(r["path"], files)
                degree = node_degrees.get(r["path"], 0)
                hub_penalty = math.log2(2 + degree)
                base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                base_score += learned.get(r["path"], 0.0)
                base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                r["_symbols"] = extract_symbols(repo_root / r["path"])
                r["_relevance"] = base_score
            filtered.sort(key=lambda x: -x.get("_relevance", x.get("weight", 0)))
        else:
            for r in filtered:
                base_score = r.get("weight", 1)
                mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                if _is_test_file(r["path"]):
                    base_score *= 0.05
                    if mirror_group.get("has_non_test"):
                        base_score *= 0.2
                elif mirror_group.get("has_test"):
                    base_score += 5
                base_score = _apply_file_class_weight(base_score, r["path"], query_intent)
                base_score *= _locality_multiplier(r["path"], files)
                degree = node_degrees.get(r["path"], 0)
                hub_penalty = math.log2(2 + degree)
                base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                base_score += learned.get(r["path"], 0.0)
                base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                r["_symbols"] = extract_symbols(repo_root / r["path"])
                r["_relevance"] = base_score
            filtered.sort(key=lambda x: -x.get("_relevance", 0))

        # Apply diversity selection (MMR) over top candidates
        filtered = _mmr_select(filtered, limit=min(len(filtered), stage1_cap), mmr_lambda=mmr_lambda)

        # --- Phase 5: Token budget (budget depends on tier) ---
        # Tier 1 (summaries) uses ~5 tokens/file for budget calc
        # Tier 2 (signatures) uses ~50 tokens/file
        # Tier 3 (chunks) uses actual token count
        budgeted: list[dict] = []
        tokens_used = 0
        for r in filtered:
            fp = repo_root / r["path"]
            if not fp.is_file():
                continue
            file_tokens = estimate_file_tokens(fp)

            # Estimate how many tokens THIS entry will add to response
            if tier == 1:
                entry_tokens = 30  # path + summary ≈ 30 tokens
            elif tier == 2:
                entry_tokens = min(200, file_tokens // 4)  # signatures ≈ 25% of file
            else:
                # Chunks are typically 15-30% of file size, capped at fallback limit.
                # Use min(file_tokens, 3000) to avoid over-consuming the budget
                # on large config/spec files where only a small section is relevant.
                entry_tokens = min(file_tokens, 3000)

            if tokens_used + entry_tokens > token_budget and budgeted:
                break
            r["tokens"] = file_tokens
            r["_entry_tokens"] = entry_tokens
            tokens_used += entry_tokens
            budgeted.append(r)

        # --- Phase 6: Build response (tier-aware) ---
        response: dict = {
            "seed_files": files,
            "context_files": [],
            "tier": tier,
            "profile": profile_name,
            "tokens_used": tokens_used,
            "token_budget": token_budget,
            "files_included": len(budgeted),
            "files_available": len(filtered),
            "iterations": iterations,
            "expansion": expansion,
        }
        if already_have:
            response["skipped_already_have"] = len(already_have)

        for r in budgeted:
            fp = repo_root / r["path"]
            entry: dict = {"path": r["path"]}

            if r.get("signal"):
                entry["signal"] = r["signal"]

            if tier == 1:
                # Tier 1: path + summary + keywords (minimal tokens)
                entry["summary"] = get_file_summary(fp)
                entry["tokens"] = r.get("tokens", 0)

            elif tier == 2:
                # Tier 2: path + signatures (function/class declarations)
                sigs = get_signatures(fp)
                entry["signatures"] = sigs[:20]  # Cap at 20 signatures
                entry["tokens"] = r.get("tokens", 0)

            else:
                # Tier 3: relevant chunks (not full file — just the parts that matter)
                seed_imports: list[str] = []
                for seed in files:
                    seed_path = repo_root / seed
                    if seed_path.is_file():
                        seed_imports.extend(extract_imports(seed_path))

                chunks = get_relevant_chunks(fp, query=query, seed_imports=seed_imports)
                if chunks:
                    entry["chunks"] = [
                        {"name": c["name"], "lines": f"{c['start_line']}-{c['end_line']}", "body": c["body"]}
                        for c in chunks
                    ]
                else:
                    # Fallback: return full content if no chunks detected
                    try:
                        content = fp.read_text(errors="ignore")
                        if len(content) > 10000:
                            content = content[:10000] + "\n# ... truncated (use tier=2 for overview)"
                        entry["content"] = content
                    except OSError:
                        entry["content"] = ""
                entry["tokens"] = r.get("tokens", 0)

            response["context_files"].append(entry)

            if query:
                store.log_feedback(query=query, file_path=r["path"], returned=True, used=False)

        # Clean internal keys
        for r in budgeted:
            r.pop("_relevance", None)
            r.pop("_entry_tokens", None)
            r.pop("_symbols", None)

        # Report actual payload size so the AI gets an honest token count.
        # Phase 5 uses file-size estimates for gating; here we measure reality.
        if not no_safety:
            response = redact_context_payload(response)
        response = run_post_context_hooks(repo_root, response)
        actual_chars = len(json.dumps(response["context_files"]))
        response["tokens_used"] = actual_chars // BYTES_PER_TOKEN
        store.close()

        return json.dumps(response)

    return json.dumps({"error": f"Unknown command: {command}"})


def _send(msg: dict) -> None:
    """Write a JSON-RPC message to stdout."""
    raw = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(raw)}\r\n\r\n{raw}")
    sys.stdout.flush()


def _read() -> dict | None:
    """Read a JSON-RPC message from stdin."""
    # Read Content-Length header
    while True:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if line.startswith("Content-Length:"):
            length = int(line.split(":")[1].strip())
            break
        if line == "":
            continue

    # Read blank line separator
    sys.stdin.readline()

    # Read body
    body = sys.stdin.read(length)
    return json.loads(body)


class CodeGraphServer:
    """Backward-compatible wrapper used by legacy tests and scripts."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    async def _handle_tool_call(self, name: str, arguments: dict) -> list[dict]:
        mapped_name = name
        mapped_args = dict(arguments)

        if name == "tier_1_search":
            mapped_name = "code_graph"
            mapped_args = {
                "command": "context",
                "files": [arguments.get("seed_path", "")],
                "query": arguments.get("query", ""),
                "tier": 1,
            }
        elif name == "tier_3_search":
            mapped_name = "code_graph"
            mapped_args = {
                "command": "context",
                "files": [arguments.get("seed_path", "")],
                "query": arguments.get("query", ""),
                "tier": 3,
            }

        text = _handle_tool_call(self.repo_root, mapped_name, mapped_args)
        return [{"type": "text", "text": text}]


def run_server(repo_root: Path, transport: str = "stdio") -> None:
    """Run the MCP server over stdio."""
    while True:
        msg = _read()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "code-graph-mcp",
                        "version": "0.1.0",
                    },
                },
            })

        elif method == "notifications/initialized":
            pass  # no response needed

        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result_text = _handle_tool_call(repo_root, tool_name, arguments)
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                    },
                })
            except Exception as e:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })

        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        elif msg_id is not None:
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })
