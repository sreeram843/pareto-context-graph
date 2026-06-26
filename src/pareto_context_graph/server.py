"""Minimal MCP server exposing pareto-context-graph via stdio.

Single unified tool (`pareto_context_graph`) to minimize schema token overhead.
Zero-dependency MCP implementation using JSON-RPC 2.0 over stdin/stdout.
"""

from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path

from .adaptive_cap import adaptive_stage1_cap
from .audit import log_audit_event
from .blast import (
    blast_radius,
    extract_dbt_refs,
    extract_imports,
    filter_existing,
    find_directory_siblings,
    find_naming_pairs,
    resolve_dbt_ref_to_file,
    resolve_import_to_file,
)
from .cancellation import cancel as cancel_request
from .cancellation import clear as clear_cancel
from .cancellation import register as register_cancel
from .chunks import (
    extract_symbols,
)
from .community import (
    community_membership_map,
    community_rank_boost,
    detect_communities,
)

# Single tool — reduces schema overhead from ~1000 tokens to ~200 tokens
from .context_confidence import build_retrieval_confidence
from .context_ranking import (
    all_repo_files as _all_repo_files,
)
from .context_ranking import (
    apply_file_class_weight as _apply_file_class_weight,
)
from .context_ranking import (
    build_context_entry as _build_context_entry,
)
from .context_ranking import (
    build_mirror_groups as _build_mirror_groups,
)
from .context_ranking import (
    candidate_features as _candidate_features,
)
from .context_ranking import (
    entry_diagnostics as _entry_diagnostics,
)
from .context_ranking import (
    learned_weights as _learned_weights,
)
from .context_ranking import (
    locality_multiplier as _locality_multiplier,
)
from .context_ranking import (
    mirror_key as _mirror_key,
)
from .context_ranking import (
    mmr_select as _mmr_select,
)
from .context_ranking import (
    node_degrees as _node_degrees,
)
from .context_ranking import (
    redact_entry_fields as _redact_entry_fields,
)
from .context_ranking import (
    semantic_search as _semantic_search,
)
from .context_ranking import (
    stage1_candidates as _stage1_candidates,
)
from .deadlines import (
    DEFAULT_TIMEOUT_MS,
    HIGH_FANOUT_DEGREE_THRESHOLD,
    LARGE_REPO_GRAPH_FILES,
    MAX_SYMBOL_FILE_READS,
    RequestDeadline,
    clear_current_cancel_event,
    current_cancel_event,
    deadline_tick,
    set_current_cancel_event,
)
from .edge_decay import maybe_decay_cochange_edges
from .embed import query_embedding_scores
from .features import feature_enabled, request_flag
from .feedback import FeedbackEventLog, feedback_path_signals, log_context_request, record_feedback
from .graph import get_changed_files, incremental_update
from .hooks import (
    redact_context_payload,
    run_post_build_hooks,
    run_post_context_hooks,
    run_post_update_hooks,
    run_pre_context_hooks,
)
from .metrics import METRICS, ContextPhaseTracker, PhaseTimer
from .orchestrator import retrieve as orchestrator_retrieve
from .policy import apply_context_policy, default_profile
from .pool import close_store_pool, get_store_pool, open_store
from .profiles import autodetect_profile, resolve_profile
from .ranker import apply_ranker_boost, blend_alpha, load_ranker
from .repo_caches import invalidate_caches
from .savings import build_context_savings
from .selective_hybrid import (
    allow_seed_hybrid,
    allow_semantic_hybrid,
    prefer_bm25_for_semantic,
    semantic_top_n,
)
from .session import (
    clear_session,
    merge_session_already_have,
    record_session_paths,
    session_memory_enabled,
)
from .store import Store
from .suggested_next import build_suggested_next
from .taxonomy import classify_query_intent
from .taxonomy import is_noise_path as _is_noise_path
from .taxonomy import is_test_file as _is_test_file
from .taxonomy import query_is_test_focused as _query_is_test_focused
from .tokenizer import count_json, resolve_tokenizer
from .tokens import compute_savings, estimate_file_tokens
from .tracing import begin_context_trace, end_context_trace

TOOLS = [
    {
        "name": "pareto_context_graph",
        "description": "Git-aware code intelligence. Commands: context (get related files for any prompt), retrieve (verbatim payload by content_hash), build (build graph), update (incremental update), blast (files affected by diff), neighbours (co-change lookup), search (find files), stats, doctor, hotspots, communities, session_clear, decay_sweep, mark_used, feedback_cite, feedback_accept, feedback_reject, feedback_view, feedback_dwell.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "One of: context, retrieve, build, update, blast, neighbours, search, stats, doctor, hotspots, communities, session_clear, savings, decay_sweep, mark_used, feedback_cite, feedback_accept, feedback_reject, feedback_view, feedback_dwell",
                    "enum": [
                        "context",
                        "retrieve",
                        "build",
                        "update",
                        "blast",
                        "neighbours",
                        "search",
                        "stats",
                        "doctor",
                        "hotspots",
                        "communities",
                        "session_clear",
                        "savings",
                        "decay_sweep",
                        "mark_used",
                        "feedback_cite",
                        "feedback_accept",
                        "feedback_reject",
                        "feedback_view",
                        "feedback_dwell",
                    ],
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
                "session_memory": {
                    "type": "boolean",
                    "description": "[context] Merge .pareto-context-graph/session.json into already_have (default on; PCG_FEATURE_SESSION_MEMORY=0 to disable)",
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
                    "description": '[build] Optional git --since expression (e.g. "12 months ago" or "2025-01-01")',
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
                    "description": "[mark_used/feedback_*] Context file paths for feedback",
                },
                "request_id": {
                    "type": "string",
                    "description": "[context] Server-assigned id echoed in response; [feedback_*] Required id from a prior context call",
                },
                "dwell_seconds": {
                    "type": "number",
                    "description": "[feedback_dwell] Seconds the user viewed a file (≥30 counts as positive)",
                },
                "no_safety": {
                    "type": "boolean",
                    "description": "[context] Disable secret redaction on returned content",
                },
                "tokenizer": {
                    "type": "string",
                    "description": "[context] Token counter: auto (default), legacy, cl100k_base, o200k_base, or tiktoken:<encoding>",
                },
                "compression": {
                    "type": "string",
                    "description": "[context] none (default), lossy (tier-2 private signatures), prune (query-aware tier-3 trim + cache), aggressive (stronger prune)",
                    "enum": ["none", "lossy", "prune", "aggressive"],
                    "default": "none",
                },
                "content_hash": {
                    "type": "string",
                    "description": "[retrieve] SHA-256 hash from a prior context response with compression=prune|aggressive",
                },
                "query_first": {
                    "type": "boolean",
                    "description": "[context] Allow query-only context (default on; PCG_FEATURE_QUERY_FIRST=0 to disable)",
                },
                "diagnostics": {
                    "type": "boolean",
                    "description": "[context] Include per-candidate retrieval scores (default on; PCG_FEATURE_DIAGNOSTICS=0 to disable)",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "[context] Per-request deadline in milliseconds (default: 5000)",
                    "default": DEFAULT_TIMEOUT_MS,
                },
            },
            "required": ["command"],
        },
    },
]


_FEEDBACK_COMMAND_KINDS = {
    "feedback_cite": "cite",
    "feedback_accept": "accept",
    "feedback_reject": "reject",
    "feedback_view": "view",
    "feedback_dwell": "dwell",
}


def _handle_tool_call(repo_root: Path, name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""

    # Support both old multi-tool names and new unified tool
    if name == "pareto_context_graph":
        command = arguments.get("command", "")
    else:
        # Backwards compatibility: map old tool names to commands
        _name_map = {
            "build_graph": "build",
            "update_graph": "update",
            "get_blast_radius": "blast",
            "get_token_savings": "savings",
            "get_neighbours": "neighbours",
            "get_graph_stats": "stats",
            "get_hotspots": "hotspots",
            "search_graph": "search",
            "get_communities": "communities",
            "get_context": "context",
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
        invalidate_caches()
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
        invalidate_caches()
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
        invalidate_caches()
        return json.dumps(result)

    # --- blast ---
    if command == "blast":
        base = arguments.get("base", "main")
        min_weight = arguments.get("min_weight", 2)
        max_depth = arguments.get("max_depth", 2)

        store = Store(repo_root)
        changed = get_changed_files(repo_root, base=base)
        results = blast_radius(
            store,
            changed,
            min_weight=min_weight,
            max_depth=max_depth,
            use_structural=feature_enabled("STRUCTURAL_EDGES"),
        )
        existing = filter_existing(repo_root, [r["path"] for r in results])
        filtered = [r for r in results if r["path"] in existing]
        store.close()

        return json.dumps(
            {
                "changed": changed,
                "affected": [r["path"] for r in filtered if r["depth"] > 0],
                "total": len(existing),
            }
        )

    # --- savings ---
    if command == "savings":
        base = arguments.get("base", "main")
        store = Store(repo_root)
        changed = get_changed_files(repo_root, base=base)
        results = blast_radius(store, changed)
        existing = filter_existing(repo_root, [r["path"] for r in results])
        savings = compute_savings(repo_root, existing)
        store.close()
        return json.dumps(
            {
                "blast_tokens": savings["blast_tokens"],
                "saved_tokens": savings["saved_tokens"],
                "reduction": f"{savings['percent_reduction']}%",
                "efficiency": f"{savings['multiplier']}x",
            }
        )

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
        result.update(
            {
                "last_build_commits": store.get_meta("last_build_commits"),
                "total_commits_scanned": store.get_meta("total_commits_scanned"),
                "build_strategy": store.get_meta("build_strategy"),
                "last_build_since": store.get_meta("last_build_since"),
            }
        )
        store.close()
        return json.dumps(result)

    # --- doctor ---
    if command == "doctor":
        from .doctor import gather_doctor_report

        return json.dumps(
            gather_doctor_report(
                repo_root,
                profile=arguments.get("profile"),
                commits=arguments.get("commits"),
                since=arguments.get("since"),
                shards=arguments.get("shards"),
            )
        )

    # --- feedback events ---
    if command in _FEEDBACK_COMMAND_KINDS:
        request_id = str(arguments.get("request_id", ""))
        paths = list(arguments.get("paths", []) or [])
        if not request_id:
            return json.dumps({"error": "request_id is required"})
        if not paths:
            return json.dumps({"error": "paths is required"})
        kind = _FEEDBACK_COMMAND_KINDS[command]
        dwell = arguments.get("dwell_seconds")
        if kind == "dwell" and dwell is None:
            return json.dumps({"error": "dwell_seconds is required for feedback_dwell"})
        stats = record_feedback(
            repo_root,
            kind=kind,
            request_id=request_id,
            paths=paths,
            query=str(arguments.get("query", "")),
            dwell_seconds=float(dwell) if dwell is not None else None,
        )
        return json.dumps({"request_id": request_id, **stats})

    # --- mark_used ---
    if command == "mark_used":
        paths = list(arguments.get("paths", []) or [])
        request_id = str(arguments.get("request_id", ""))
        store = Store(repo_root)
        updated = store.mark_feedback_used(paths)
        store.close()
        event_stats = {"written": 0, "deduped": 0}
        if paths:
            event_stats = record_feedback(
                repo_root,
                kind="mark_used",
                request_id=request_id or "legacy",
                paths=paths,
                query=str(arguments.get("query", "")),
            )
        return json.dumps({"updated": updated, **event_stats, "request_id": request_id or None})

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
        if not query:
            return json.dumps({"error": "query is required"})
        limit = arguments.get("limit", 20)
        with PhaseTimer("search"):
            with open_store(repo_root, write=False) as store:
                payload = store.unified_search(query, limit=limit)
        log_audit_event(
            repo_root,
            command="search",
            query=query,
            returned_paths=int(payload.get("count", 0)),
        )
        METRICS.inc("cgmcp_retriever_hits_total", retriever="search")
        return json.dumps(payload)

    # --- communities ---
    if command == "communities":
        min_weight = arguments.get("min_weight", 3)
        profile_name = arguments.get("profile") or autodetect_profile(repo_root)
        store = Store(repo_root)
        payload = detect_communities(
            store,
            profile_name=profile_name,
            min_weight=min_weight,
            use_leiden=feature_enabled("LEIDEN"),
        )
        payload.pop("_all_communities", None)
        store.close()
        return json.dumps(payload)

    # --- session_clear ---
    if command == "session_clear":
        clear_session(repo_root)
        return json.dumps({"cleared": True, "session_file": ".pareto-context-graph/session.json"})

    # --- retrieve (verbatim payload from prune compression cache) ---
    if command == "retrieve":
        from .payload_compress import retrieve_payload

        content_hash = str(arguments.get("content_hash", "")).strip()
        if not content_hash:
            return json.dumps({"error": "content_hash is required"})
        payload = retrieve_payload(repo_root, content_hash)
        if payload is None:
            return json.dumps(
                {
                    "error": "payload not found",
                    "content_hash": content_hash,
                    "hint": "Run context with compression=prune or aggressive first",
                }
            )
        return json.dumps(
            {
                "content_hash": content_hash,
                "payload": payload,
            }
        )

    # --- context (primary tool — 6-phase pipeline with tiers) ---
    if command == "context":
        arguments = apply_context_policy(repo_root, arguments)
        arguments = run_pre_context_hooks(repo_root, arguments)
        request_id = str(arguments.get("request_id") or uuid.uuid4())
        seed_files = list(arguments.get("files", []) or [])
        query = arguments.get("query", "")
        query_first = request_flag(arguments, "query_first", "QUERY_FIRST")
        diagnostics = request_flag(arguments, "diagnostics", "DIAGNOSTICS")

        if not seed_files and not query:
            return json.dumps({"error": "files or query is required"})
        if not seed_files and not query_first:
            return json.dumps(
                {
                    "error": (
                        "files parameter is required "
                        "(query-first is default; set PCG_FEATURE_QUERY_FIRST=0 to require seed files)"
                    )
                }
            )

        profile_name = (
            arguments.get("profile") or default_profile(repo_root) or autodetect_profile(repo_root)
        )
        profile = resolve_profile(profile_name)
        files = list(seed_files)
        token_budget = arguments.get("token_budget", 50000)
        tier = arguments.get("tier", 1)
        already_have = set(arguments.get("already_have", []))
        session_merged = 0
        already_have, session_merged = merge_session_already_have(
            repo_root, already_have, arguments
        )
        deadline = RequestDeadline(
            int(arguments.get("timeout_ms", DEFAULT_TIMEOUT_MS)),
            cancel_event=current_cancel_event(),
        )
        truncated = False
        symbol_reads = 0
        timed_out_phase = ""
        feedback_paths: list[str] = []
        min_weight = arguments.get("min_weight", profile.get("min_weight", 2))
        max_depth = arguments.get("max_depth", profile.get("max_depth", 2))
        profile_stage1_cap = int(profile.get("stage1_cap", 500))
        if "stage1_cap" in arguments:
            stage1_cap = int(arguments["stage1_cap"])
        else:
            stage1_cap = profile_stage1_cap
        expansion = arguments.get("expansion", profile.get("expansion", "bfs"))
        iterations = int(arguments.get("iterations", profile.get("iterations", 1)))
        hub_penalty_strength = float(
            arguments.get("hub_penalty_strength", profile.get("hub_penalty_strength", 1.0))
        )
        mmr_lambda = float(arguments.get("mmr_lambda", profile.get("mmr_lambda", 0.7)))
        no_safety = bool(arguments.get("no_safety", False))
        compression = arguments.get("compression", "none")
        if compression not in ("none", "lossy", "prune", "aggressive"):
            compression = "none"
        tokenizer_name = arguments.get("tokenizer")
        try:
            tokenizer = resolve_tokenizer(tokenizer_name)
        except (ImportError, ValueError) as exc:
            return json.dumps({"error": str(exc)})

        _pool_read = get_store_pool(repo_root).read()
        store = _pool_read.__enter__()
        try:
            with get_store_pool(repo_root).write() as decay_store:
                maybe_decay_cochange_edges(decay_store)
            sparse_graph = store.edge_count() < 500
            query_only = not seed_files and bool(query) and query_first
            large_graph = store.file_count() >= LARGE_REPO_GRAPH_FILES
            high_fanout = False
            max_seed_degree = 0
            if seed_files:
                max_seed_degree = max(store.file_degree(path) for path in seed_files)
                high_fanout = max_seed_degree >= HIGH_FANOUT_DEGREE_THRESHOLD
                if high_fanout:
                    expansion = "bfs"
                    iterations = 1
                    max_depth = min(max_depth, 1)
                    stage1_cap = adaptive_stage1_cap(
                        query, profile_cap=stage1_cap, high_fanout=True
                    )
                elif "stage1_cap" not in arguments:
                    stage1_cap = adaptive_stage1_cap(
                        query, profile_cap=stage1_cap, high_fanout=False
                    )
            elif "stage1_cap" not in arguments:
                stage1_cap = adaptive_stage1_cap(query, profile_cap=stage1_cap, high_fanout=False)
            skip_expensive_hybrid = not allow_seed_hybrid(
                high_fanout=high_fanout,
                large_graph=large_graph,
            )
            run_semantic_hybrid = allow_semantic_hybrid(
                query=query,
                high_fanout=high_fanout,
                large_graph=large_graph,
                query_only=query_only,
            )

            def _timed_out(phase: str) -> bool:
                nonlocal truncated, timed_out_phase
                if deadline.expired():
                    truncated = True
                    timed_out_phase = phase
                    return True
                return False

            def _symbols_for(path: Path) -> list[str]:
                nonlocal symbol_reads, truncated
                if symbol_reads >= MAX_SYMBOL_FILE_READS:
                    truncated = True
                    return []
                symbol_reads += 1
                return extract_symbols(path)

            phase_tracker = ContextPhaseTracker()
            begin_context_trace(
                request_id,
                query=(query[:120] if query else ""),
                profile=str(profile_name),
            )
            phase_tracker.enter("retrieve")

            orchestrator_hits: list[dict] = []
            if query_only and not _timed_out("retrieve"):
                orchestrator_hits = orchestrator_retrieve(
                    repo_root, store, query, [], limit=stage1_cap
                )
                if not orchestrator_hits:
                    phase_tracker.close_active()
                    end_context_trace()
                    return json.dumps(
                        {
                            "error": "query-first retrieval found no candidates; rebuild the graph index",
                        }
                    )

            # --- Phase 1: candidate generation (BFS/RWR, optionally iterative) ---
            stage1_results: list[dict] = []
            if not _timed_out("retrieve"):
                if query_only:
                    for hit in orchestrator_hits:
                        path = hit["path"]
                        weight = max(10, int(hit["score"] * 1000))
                        if _is_noise_path(path):
                            weight = max(1, weight // 20)
                        stage1_results.append(
                            {
                                "path": path,
                                "depth": 1,
                                "weight": weight,
                                "source": "orchestrator",
                                "signal": "query_first",
                                "_features": hit.get("features", {}),
                                "_orchestrator_score": hit["score"],
                            }
                        )
                    files = [
                        hit["path"] for hit in orchestrator_hits if not _is_noise_path(hit["path"])
                    ][:3]
                else:
                    seed_pool = list(files)
                    for round_idx in range(max(1, iterations)):
                        if _timed_out("retrieve"):
                            break
                        generated = _stage1_candidates(
                            store,
                            seed_pool,
                            min_weight=min_weight,
                            max_depth=max_depth,
                            cap=stage1_cap,
                            expansion=expansion,
                            expired_check=lambda: _timed_out("retrieve"),
                        )
                        existing_paths = {item["path"] for item in stage1_results}
                        additions = [g for g in generated if g["path"] not in existing_paths]
                        stage1_results.extend(additions)
                        if not additions:
                            break
                        seed_pool = [a["path"] for a in additions[: min(25, len(additions))]]

            results = stage1_results

            if not _timed_out("retrieve") and orchestrator_hits and not query_only:
                seen_paths = {item["path"] for item in results}
                for hit in orchestrator_hits:
                    path = hit["path"]
                    if path in seen_paths:
                        continue
                    results.insert(
                        0,
                        {
                            "path": path,
                            "depth": 0,
                            "weight": max(1, int(hit["score"] * 100)),
                            "source": "orchestrator",
                            "signal": "query_first",
                            "_features": hit.get("features", {}),
                            "_orchestrator_score": hit["score"],
                        },
                    )
                    seen_paths.add(path)

            phase_tracker.enter("hybrid")

            # --- Phase 2: Hybrid signals ---
            all_files_set: set[str] = set()
            if not skip_expensive_hybrid:
                all_files_set = set(store.all_files())
                if sparse_graph:
                    all_files_set |= _all_repo_files(repo_root)
            hybrid_additions: list[dict] = []

            if not _timed_out("hybrid") and not skip_expensive_hybrid:
                for seed in files:
                    if _timed_out("hybrid"):
                        break
                    seed_path = repo_root / seed
                    if seed_path.is_file():
                        imports = extract_imports(seed_path)
                        for ref_idx, ref in enumerate(imports):
                            if deadline_tick(ref_idx) and _timed_out("hybrid"):
                                break
                            resolved = resolve_import_to_file(ref, all_files_set, from_path=seed)
                            if resolved and resolved not in {r["path"] for r in results}:
                                hybrid_additions.append(
                                    {
                                        "path": resolved,
                                        "depth": 1,
                                        "weight": 1,
                                        "source": seed,
                                        "signal": "import",
                                    }
                                )

                        # dbt {{ ref() }} and {{ source() }} resolution (SQL/Jinja files)
                        if seed_path.suffix in {".sql", ".jinja", ".jinja2", ".j2"}:
                            dbt_refs = extract_dbt_refs(seed_path)
                            for ref_name in dbt_refs:
                                if _timed_out("hybrid"):
                                    break
                                resolved = resolve_dbt_ref_to_file(ref_name, all_files_set)
                                if resolved and resolved not in {r["path"] for r in results}:
                                    hybrid_additions.append(
                                        {
                                            "path": resolved,
                                            "depth": 1,
                                            "weight": 3,
                                            "source": seed,
                                            "signal": "dbt_ref",
                                        }
                                    )

                            # Reverse dbt ref scan is O(repo); skip on large graphs.
                            if not large_graph:
                                seed_stem = seed_path.stem.lower()
                                for cand_idx, candidate in enumerate(all_files_set):
                                    if deadline_tick(cand_idx) and _timed_out("hybrid"):
                                        break
                                    if candidate == seed or not candidate.endswith(".sql"):
                                        continue
                                    cand_path = repo_root / candidate
                                    if not cand_path.is_file():
                                        continue
                                    try:
                                        snippet = cand_path.read_text(errors="ignore")[:5000]
                                    except OSError:
                                        continue
                                    if (
                                        f"ref('{seed_path.stem}')" in snippet
                                        or f'ref("{seed_path.stem}")' in snippet
                                    ):
                                        if candidate not in {r["path"] for r in results}:
                                            hybrid_additions.append(
                                                {
                                                    "path": candidate,
                                                    "depth": 1,
                                                    "weight": 3,
                                                    "source": seed,
                                                    "signal": "dbt_ref_reverse",
                                                }
                                            )

                    if not skip_expensive_hybrid:
                        pairs = find_naming_pairs(seed, all_files_set)
                        for p in pairs:
                            if p not in {r["path"] for r in results}:
                                hybrid_additions.append(
                                    {
                                        "path": p,
                                        "depth": 1,
                                        "weight": 1,
                                        "source": seed,
                                        "signal": "naming",
                                    }
                                )

                        siblings = find_directory_siblings(seed, all_files_set)
                        for sibling in siblings:
                            if sibling not in {r["path"] for r in results}:
                                sibling_weight = 2 if sparse_graph else 1
                                hybrid_additions.append(
                                    {
                                        "path": sibling,
                                        "depth": 1,
                                        "weight": sibling_weight,
                                        "source": seed,
                                        "signal": "directory",
                                    }
                                )

            phase_tracker.enter("semantic")

            # Phase 2b: Semantic content matching (BM25 or TF-IDF fallback)
            if run_semantic_hybrid and not _timed_out("semantic"):
                kw_top_n = semantic_top_n(large_graph=large_graph, query_only=query_only)
                kw_results = _semantic_search(
                    repo_root,
                    store,
                    query,
                    top_n=kw_top_n,
                    prefer_bm25=prefer_bm25_for_semantic(
                        large_graph=large_graph,
                        query_only=query_only,
                    )
                    or store.has_search_index(),
                    large_graph=large_graph and query_only,
                )
                existing_paths = {r["path"] for r in results} | {
                    h["path"] for h in hybrid_additions
                }
                for kw_path, kw_score in kw_results:
                    if kw_path not in existing_paths and kw_path not in files:
                        hybrid_additions.append(
                            {
                                "path": kw_path,
                                "depth": 2,
                                "weight": max(1, int(kw_score / 10)),
                                "source": "keyword_match",
                                "signal": "semantic",
                            }
                        )

            # Merge hybrid results
            seen = {r["path"] for r in results}
            for h in hybrid_additions:
                if h["path"] not in seen:
                    results.append(h)
                    seen.add(h["path"])

            phase_tracker.enter("filter")

            # --- Phase 3: Filter existing + delta context ---
            existing_paths: list[str] = []
            if not _timed_out("filter"):
                for idx, path in enumerate([r["path"] for r in results]):
                    if _timed_out("filter"):
                        break
                    if (repo_root / path).is_file():
                        existing_paths.append(path)
            existing_set = set(existing_paths)
            filtered = [
                r
                for r in results
                if r["path"] in existing_set
                and (r["depth"] > 0 or r.get("signal") == "query_first")
                and r["path"] not in already_have
            ]

            phase_tracker.enter("rank")

            # --- Phase 4: Query-aware ranking ---
            node_degrees: dict[str, int] = {}
            learned: dict[str, float] = {}
            embed_scores: dict[str, float] = {}
            community_membership: dict[str, int] = {}
            if not _timed_out("rank"):
                if feature_enabled("COMMUNITY_RANK") and files and not large_graph:
                    community_membership = community_membership_map(
                        store,
                        profile_name=profile_name,
                        use_leiden=feature_enabled("LEIDEN"),
                    )
                if high_fanout:
                    for idx, r in enumerate(filtered):
                        if deadline_tick(idx) and _timed_out("rank"):
                            break
                        r["_relevance"] = float(r.get("weight", 1))
                    filtered.sort(key=lambda x: -x.get("_relevance", 0))
                else:
                    test_focused = _query_is_test_focused(query) if query else False
                    query_intent = classify_query_intent(query) if query else "default"
                    if large_graph:
                        node_degrees = {}
                    else:
                        node_degrees = _node_degrees(repo_root, store)

                    def _path_degree(path: str) -> int:
                        if path not in node_degrees:
                            node_degrees[path] = store.file_degree(path)
                        return node_degrees[path]

                    learned = _learned_weights(repo_root)
                    if query and not _timed_out("rank"):
                        embed_scores = query_embedding_scores(
                            repo_root, query, [r["path"] for r in filtered]
                        )
                    mirror_groups = _build_mirror_groups([r["path"] for r in filtered])
                    if query:
                        query_lower = query.lower()
                        query_terms = set(query_lower.split())
                        for idx, r in enumerate(filtered):
                            if deadline_tick(idx) and _timed_out("rank"):
                                break
                            path_lower = r["path"].lower()
                            term_hits = sum(1 for t in query_terms if t in path_lower)
                            base_score = r.get("weight", 1) + (term_hits * 10)
                            mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                            if not test_focused and _is_test_file(r["path"]):
                                base_score *= 0.05
                                if mirror_group.get("has_non_test"):
                                    base_score *= 0.2
                            elif _is_noise_path(r["path"]):
                                base_score *= 0.05
                            elif mirror_group.get("has_test"):
                                base_score += 5
                            base_score = _apply_file_class_weight(
                                base_score, r["path"], query_intent
                            )
                            base_score *= _locality_multiplier(r["path"], files)
                            degree = _path_degree(r["path"])
                            hub_penalty = math.log2(2 + degree)
                            base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                            base_score += learned.get(r["path"], 0.0)
                            base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                            comm_boost = community_rank_boost(
                                r["path"], files, community_membership
                            )
                            base_score += comm_boost
                            r["_community_boost"] = round(comm_boost, 4)
                            r["_symbols"] = _symbols_for(repo_root / r["path"])
                            r["_relevance"] = base_score
                        filtered.sort(key=lambda x: -x.get("_relevance", x.get("weight", 0)))
                    else:
                        for idx, r in enumerate(filtered):
                            if deadline_tick(idx) and _timed_out("rank"):
                                break
                            base_score = r.get("weight", 1)
                            mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                            if _is_test_file(r["path"]):
                                base_score *= 0.05
                                if mirror_group.get("has_non_test"):
                                    base_score *= 0.2
                            elif _is_noise_path(r["path"]):
                                base_score *= 0.05
                            elif mirror_group.get("has_test"):
                                base_score += 5
                            base_score = _apply_file_class_weight(
                                base_score, r["path"], query_intent
                            )
                            base_score *= _locality_multiplier(r["path"], files)
                            degree = _path_degree(r["path"])
                            hub_penalty = math.log2(2 + degree)
                            base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                            base_score += learned.get(r["path"], 0.0)
                            base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                            comm_boost = community_rank_boost(
                                r["path"], files, community_membership
                            )
                            base_score += comm_boost
                            r["_community_boost"] = round(comm_boost, 4)
                            r["_symbols"] = _symbols_for(repo_root / r["path"])
                            r["_relevance"] = base_score
                        filtered.sort(key=lambda x: -x.get("_relevance", 0))

                    ranker = load_ranker(repo_root)
                    feedback_alpha = blend_alpha(len(FeedbackEventLog(repo_root).read_all()))
                    feedback_signals = feedback_path_signals(repo_root)
                    for idx, r in enumerate(filtered):
                        if deadline_tick(idx) and _timed_out("rank"):
                            break
                        feats = _candidate_features(
                            r,
                            files=files,
                            node_degrees=node_degrees,
                            learned=learned,
                            embed_scores=embed_scores,
                            hub_penalty_strength=hub_penalty_strength,
                            already_have=already_have,
                            feedback_signals=feedback_signals,
                        )
                        r["_features"] = feats
                        if ranker and feedback_alpha > 0:
                            r["_relevance"] = apply_ranker_boost(
                                float(r.get("_relevance", r.get("weight", 0))),
                                feats,
                                ranker,
                                alpha=feedback_alpha,
                            )
                    if ranker and feedback_alpha > 0:
                        filtered.sort(key=lambda x: -x.get("_relevance", 0))
            else:
                for r in filtered:
                    r["_relevance"] = r.get("weight", 1)

            # Apply diversity selection (MMR) over top candidates
            if high_fanout:
                filtered = filtered[: min(len(filtered), stage1_cap)]
            else:
                filtered = _mmr_select(
                    filtered,
                    limit=min(len(filtered), stage1_cap),
                    mmr_lambda=mmr_lambda,
                    expired_check=lambda: _timed_out("rank"),
                )

            phase_tracker.enter("pack")

            # --- Phase 5+6: Incremental packing with honest tokenizer counts ---
            rank_diag_ctx = {
                "files": files,
                "node_degrees": node_degrees,
                "learned": learned,
                "embed_scores": embed_scores,
                "hub_penalty_strength": hub_penalty_strength,
            }
            context_files: list[dict] = []
            tokens_used = 0
            included_paths: set[str] = set()
            budget_exhausted = False

            max_pack_files = 25 if high_fanout else len(filtered)
            for pack_idx, r in enumerate(filtered):
                if pack_idx >= max_pack_files:
                    break
                if _timed_out("pack"):
                    break
                fp = repo_root / r["path"]
                if not fp.is_file():
                    continue
                r["tokens"] = estimate_file_tokens(fp)

                entry = _build_context_entry(r, repo_root, tier, query, files, compression)
                if not no_safety:
                    _redact_entry_fields(entry)

                entry_tokens = count_json(tokenizer, entry)
                if tokens_used + entry_tokens > token_budget:
                    budget_exhausted = True
                    break

                entry["tokens_actual"] = entry_tokens
                if diagnostics:
                    entry["diagnostics"] = _entry_diagnostics(r, **rank_diag_ctx)
                tokens_used += entry_tokens
                context_files.append(entry)
                included_paths.add(r["path"])

                if query:
                    feedback_paths.append(r["path"])

            from .prune_learn import (
                apply_learned_tier1_prune,
                learned_tier1_prune_enabled,
                load_prune_weights,
            )
            from .summary_prune import apply_summary_prune

            def _refresh_pack_state() -> None:
                nonlocal tokens_used, included_paths, feedback_paths
                tokens_used = sum(
                    int(entry.get("tokens_actual", count_json(tokenizer, entry)))
                    for entry in context_files
                )
                included_paths = {entry["path"] for entry in context_files}
                feedback_paths = [p for p in feedback_paths if p in included_paths]

            learned_tier1_meta: dict = {}
            if tier == 1:
                tier1_active = (
                    bool(arguments["learned_tier1_prune"])
                    if "learned_tier1_prune" in arguments
                    else learned_tier1_prune_enabled(repo_root)
                )
                if tier1_active:
                    prune_weights = load_prune_weights(repo_root)
                    if prune_weights:
                        context_files, learned_tier1_meta = apply_learned_tier1_prune(
                            context_files,
                            tier=tier,
                            prune_weights=prune_weights,
                            seed_files=files,
                        )
                        if learned_tier1_meta.get("dropped_count", 0) > 0:
                            _refresh_pack_state()

            summary_prune_meta: dict = {}
            if tier == 1 and query and request_flag(arguments, "summary_prune", "SUMMARY_PRUNE"):
                context_files, summary_prune_meta = apply_summary_prune(
                    context_files,
                    query=query,
                    tier=tier,
                    seed_files=files,
                )
                if summary_prune_meta.get("dropped_count", 0) > 0:
                    _refresh_pack_state()

            dropped_paths = [
                r["path"]
                for r in filtered
                if r["path"] not in included_paths and (repo_root / r["path"]).is_file()
            ]

            dropped_lookup = {r["path"]: r for r in filtered}
            counterfactual_candidates = [
                {
                    "path": r["path"],
                    "score": float(r.get("_relevance", r.get("weight", 0))),
                    "features": dict(r.get("_features") or {}),
                    "included": r["path"] in included_paths,
                }
                for r in filtered
            ]
            for r in filtered:
                r.pop("_relevance", None)
                r.pop("_entry_tokens", None)
                r.pop("_symbols", None)
                r.pop("_features", None)
                r.pop("_orchestrator_score", None)

            response: dict = {
                "response_version": 2,
                "request_id": request_id,
                "seed_files": seed_files if seed_files else files,
                "context_files": context_files,
                "tier": tier,
                "profile": profile_name,
                "tokenizer": tokenizer.name,
                "compression": compression,
                "tokens_used": tokens_used,
                "token_budget": token_budget,
                "files_included": len(context_files),
                "files_available": len(filtered),
                "iterations": iterations,
                "expansion": expansion,
            }
            if not seed_files and query_first:
                response["query_first"] = True
            if large_graph and query_only and run_semantic_hybrid:
                response["selective_hybrid"] = True
            if diagnostics:
                response["diagnostics"] = True
            if dropped_paths:
                dropped_payload: dict = {
                    "count": len(dropped_paths),
                    "paths": dropped_paths[:10],
                    "reason": "budget_exhausted" if budget_exhausted else "unavailable",
                }
                if diagnostics:
                    dropped_payload["details"] = [
                        {
                            "path": path,
                            "reason": dropped_payload["reason"],
                            "diagnostics": _entry_diagnostics(
                                dropped_lookup.get(path, {"path": path}),
                                **rank_diag_ctx,
                            ),
                        }
                        for path in dropped_paths[:10]
                    ]
                response["dropped_candidates"] = dropped_payload
            if truncated:
                response["truncated"] = True
                response["truncated_phase"] = timed_out_phase or deadline.timed_out_phase
            if already_have:
                response["skipped_already_have"] = len(already_have)
            if session_merged:
                response["session_already_have"] = session_merged
            if summary_prune_meta:
                response["summary_prune"] = summary_prune_meta
            if learned_tier1_meta:
                response["learned_tier1_prune"] = learned_tier1_meta
            if "stage1_cap" not in arguments:
                response["stage1_cap"] = stage1_cap

            if not no_safety:
                response = redact_context_payload(response)
            response = run_post_context_hooks(repo_root, response)

            tokens_used = 0
            for entry in response["context_files"]:
                entry["tokens_actual"] = count_json(tokenizer, entry)
                tokens_used += entry["tokens_actual"]
            response["tokens_used"] = tokens_used
            if not truncated and not large_graph:
                response["context_savings"] = build_context_savings(
                    repo_root,
                    graph_tokens=tokens_used,
                    tokenizer=tokenizer.name,
                    query=query,
                    seed_files=seed_files,
                )

            from .payload_compress import apply_context_compression

            response = apply_context_compression(
                response,
                repo_root=repo_root,
                query=query,
                compression=compression,
                tokenizer=tokenizer,
            )
            if response.get("context_savings") and response.get("tokens_before_compress"):
                response["context_savings"]["graph_tokens"] = int(response["tokens_used"])

            suggested = build_suggested_next(
                tier=tier,
                context_files=response["context_files"],
                compression=compression,
                truncated=truncated,
                timed_out_phase=timed_out_phase or deadline.timed_out_phase,
            )
            if suggested:
                response["suggested_next"] = suggested

            response["retrieval_confidence"] = build_retrieval_confidence(
                sparse_graph=sparse_graph,
                truncated=truncated,
                timed_out_phase=timed_out_phase or deadline.timed_out_phase,
                query_only=query_only,
                orchestrator_hit_count=len(orchestrator_hits),
                files_included=len(context_files),
                selective_hybrid=bool(large_graph and query_only and run_semantic_hybrid),
            )

            log_context_request(
                repo_root,
                request_id=request_id,
                query=query,
                seed_files=seed_files if seed_files else files,
                candidates=counterfactual_candidates,
                returned_paths=[entry.get("path", "") for entry in response["context_files"]],
            )

            if feedback_paths:
                with open_store(repo_root, write=True) as wstore:
                    for path in feedback_paths:
                        wstore.log_feedback(query=query, file_path=path, returned=True, used=False)

            log_audit_event(
                repo_root,
                command="context",
                query=query,
                returned_paths=len(response["context_files"]),
                tokens_used=int(response["tokens_used"]),
                truncated=truncated,
                request_id=request_id,
            )
            METRICS.inc("cgmcp_feedback_events_total", kind="context")
            METRICS.inc("cgmcp_context_requests_total")
            phase_tracker.close_active()
            end_context_trace()

            if session_memory_enabled(arguments):
                record_session_paths(
                    repo_root,
                    [entry.get("path", "") for entry in response["context_files"]],
                )

            return json.dumps(response)
        finally:
            _pool_read.__exit__(None, None, None)

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


class ParetoContextGraphServer:
    """Backward-compatible wrapper used by legacy tests and scripts."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    async def _handle_tool_call(self, name: str, arguments: dict) -> list[dict]:
        mapped_name = name
        mapped_args = dict(arguments)

        if name == "tier_1_search":
            mapped_name = "pareto_context_graph"
            mapped_args = {
                "command": "context",
                "files": [arguments.get("seed_path", "")],
                "query": arguments.get("query", ""),
                "tier": 1,
            }
        elif name == "tier_3_search":
            mapped_name = "pareto_context_graph"
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
    get_store_pool(repo_root)
    try:
        while True:
            msg = _read()
            if msg is None:
                break

            method = msg.get("method", "")
            msg_id = msg.get("id")

            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {
                                "name": "pareto-context-graph",
                                "version": "0.1.0",
                            },
                        },
                    }
                )

            elif method == "notifications/initialized":
                pass  # no response needed

            elif method == "tools/list":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"tools": TOOLS},
                    }
                )

            elif method in ("notifications/cancelled", "cancelled", "$/cancelRequest"):
                params = msg.get("params", {}) or {}
                rid = params.get("requestId") or params.get("request_id") or params.get("id")
                if rid is not None:
                    cancel_request(str(rid))

            elif method == "tools/call":
                params = msg.get("params", {})
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                cancel_event = register_cancel(str(msg_id)) if msg_id is not None else None
                set_current_cancel_event(cancel_event)
                try:
                    result_text = _handle_tool_call(repo_root, tool_name, arguments)
                    _send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "content": [{"type": "text", "text": result_text}],
                            },
                        }
                    )
                except Exception as e:
                    _send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "content": [{"type": "text", "text": f"Error: {e}"}],
                                "isError": True,
                            },
                        }
                    )
                finally:
                    if msg_id is not None:
                        clear_cancel(str(msg_id))
                    clear_current_cancel_event()

            elif method == "ping":
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})

            elif msg_id is not None:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
    finally:
        close_store_pool(repo_root)
