"""Minimal MCP server exposing pareto-context-graph via stdio.

Single unified tool (`pareto_context_graph`) to minimize schema token overhead.
Zero-dependency MCP implementation using JSON-RPC 2.0 over stdin/stdout.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from .audit import log_audit_event
from .blast import (
    blast_radius,
    filter_existing,
)
from .cancellation import cancel as cancel_request
from .cancellation import clear as clear_cancel
from .cancellation import register as register_cancel
from .community import (
    detect_communities,
)

# Single tool — reduces schema overhead from ~1000 tokens to ~200 tokens
from .context_pipeline import execute_context_pipeline
from .deadlines import (
    DEFAULT_TIMEOUT_MS,
    RequestDeadline,
    clear_current_cancel_event,
    current_cancel_event,
    set_current_cancel_event,
)
from .edge_decay import maybe_decay_cochange_edges
from .features import feature_enabled, request_flag
from .feedback import record_feedback
from .graph import get_changed_files, incremental_update
from .hooks import (
    run_post_build_hooks,
    run_post_update_hooks,
    run_pre_context_hooks,
)
from .metrics import METRICS, PhaseTimer
from .policy import apply_context_policy, default_profile
from .pool import close_store_pool, get_store_pool, open_store
from .profiles import autodetect_profile, resolve_profile
from .repo_caches import invalidate_caches
from .session import (
    clear_session,
    merge_session_already_have,
)
from .store import Store
from .tokenizer import resolve_tokenizer
from .tokens import compute_savings

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
        semantic_meta: dict[str, object] = {}
        leiden_fallback = False
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
            response = execute_context_pipeline(
                repo_root=repo_root,
                store=store,
                arguments=arguments,
                request_id=request_id,
                seed_files=seed_files,
                query=query,
                query_first=query_first,
                diagnostics=diagnostics,
                profile_name=profile_name,
                profile=profile,
                files=files,
                tokenizer=tokenizer,
                already_have=already_have,
                session_merged=session_merged,
                deadline=deadline,
                token_budget=token_budget,
                tier=tier,
                min_weight=min_weight,
                max_depth=max_depth,
                stage1_cap=stage1_cap,
                expansion=expansion,
                iterations=iterations,
                hub_penalty_strength=hub_penalty_strength,
                mmr_lambda=mmr_lambda,
                no_safety=no_safety,
                compression=compression,
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
