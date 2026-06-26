"""Context pipeline — phased retrieve → rank → pack."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ablation import ablation_enabled, active_ablations
from .adaptive_cap import adaptive_stage1_cap
from .audit import log_audit_event
from .blast import (
    extract_dbt_refs,
    extract_imports,
    find_directory_siblings,
    find_naming_pairs,
    resolve_dbt_ref_to_file,
    resolve_import_to_file,
)
from .chunks import extract_symbols
from .community import community_membership_map, community_rank_boost, detect_communities
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
    compress_pick_reason as _compress_pick_reason,
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
    HIGH_FANOUT_DEGREE_THRESHOLD,
    LARGE_REPO_GRAPH_FILES,
    MAX_SYMBOL_FILE_READS,
    RequestDeadline,
    deadline_tick,
)
from .embed import query_embedding_scores
from .features import feature_enabled, request_flag
from .feedback import FeedbackEventLog, feedback_path_signals, log_context_request
from .hooks import redact_context_payload, run_post_context_hooks
from .metrics import METRICS, ContextPhaseTracker
from .orchestrator import retrieve as orchestrator_retrieve
from .payload_compress import apply_context_compression
from .pool import open_store
from .prune_learn import (
    apply_learned_tier1_prune,
    learned_tier1_prune_enabled,
    load_prune_weights,
)
from .ranker import apply_ranker_boost, blend_alpha, load_ranker
from .savings import build_context_savings
from .selective_hybrid import (
    allow_seed_hybrid,
    allow_semantic_hybrid,
    prefer_bm25_for_semantic,
    semantic_top_n,
)
from .session import record_session_paths, session_memory_enabled
from .store import Store
from .suggested_next import build_suggested_next
from .summary_prune import apply_summary_prune
from .taxonomy import (
    classify_query_intent,
)
from .taxonomy import (
    is_noise_path as _is_noise_path,
)
from .taxonomy import (
    is_test_file as _is_test_file,
)
from .taxonomy import (
    query_is_test_focused as _query_is_test_focused,
)
from .tokenizer import count_json
from .tokens import estimate_file_tokens
from .tracing import begin_context_trace, end_context_trace


@dataclass
class Candidate:
    data: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Candidate:
        return cls(data=raw)

    @property
    def path(self) -> str:
        return str(self.data["path"])

    def to_dict(self) -> dict[str, Any]:
        return self.data


@dataclass
class ContextPipelineState:
    results: list[dict[str, Any]] = field(default_factory=list)
    orchestrator_hits: list[dict[str, Any]] = field(default_factory=list)
    hybrid_additions: list[dict[str, Any]] = field(default_factory=list)
    filtered: list[dict[str, Any]] = field(default_factory=list)
    context_files: list[dict[str, Any]] = field(default_factory=list)
    semantic_meta: dict[str, object] = field(default_factory=dict)
    leiden_fallback: bool = False
    truncated: bool = False
    timed_out_phase: str = ""


@dataclass
class PipelineConfig:
    token_budget: int
    tier: int
    min_weight: int
    max_depth: int
    stage1_cap: int
    expansion: str
    iterations: int
    hub_penalty_strength: float
    mmr_lambda: float
    no_safety: bool
    compression: str
    query_first: bool
    diagnostics: bool


PHASES = ("retrieve", "hybrid", "semantic", "filter", "rank", "pack")


def apply_ablation_to_features(features: dict[str, float]) -> dict[str, float]:
    out = dict(features)
    if ablation_enabled("bm25"):
        out["bm25"] = 0.0
    if ablation_enabled("symbol"):
        out["symbol"] = 0.0
    if ablation_enabled("embed"):
        out["embed"] = 0.0
    if ablation_enabled("co_change"):
        out["co_change"] = 0.0
    if ablation_enabled("learned"):
        out["learned_boost"] = 0.0
    return out


def execute_context_pipeline(
    *,
    repo_root: Path,
    store: Store,
    arguments: dict,
    request_id: str,
    seed_files: list[str],
    query: str,
    query_first: bool,
    diagnostics: bool,
    profile_name: str,
    profile: dict,
    files: list[str],
    tokenizer: Any,
    already_have: set[str],
    session_merged: int,
    deadline: RequestDeadline,
    token_budget: int,
    tier: int,
    min_weight: int,
    max_depth: int,
    stage1_cap: int,
    expansion: str,
    iterations: int,
    hub_penalty_strength: float,
    mmr_lambda: float,
    no_safety: bool,
    compression: str,
) -> dict:
    """Run retrieve → hybrid → semantic → filter → rank → pack."""
    truncated = False
    symbol_reads = 0
    timed_out_phase = ""
    feedback_paths: list[str] = []
    semantic_meta: dict[str, object] = {}
    leiden_fallback = False
    orchestrator_hits: list[dict] = []
    results: list[dict] = []
    hybrid_additions: list[dict] = []
    all_files_set: set[str] = set()
    filtered: list[dict] = []
    node_degrees: dict[str, int] = {}
    learned: dict[str, float] = {}
    embed_scores: dict[str, float] = {}
    community_membership: dict[str, int] = {}
    rank_diag_ctx: dict[str, Any] = {}
    context_files: list[dict] = []
    tokens_used = 0
    included_paths: set[str] = set()
    budget_exhausted = False
    learned_tier1_meta: dict = {}
    summary_prune_meta: dict = {}
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
            stage1_cap = adaptive_stage1_cap(query, profile_cap=stage1_cap, high_fanout=True)
        elif "stage1_cap" not in arguments:
            stage1_cap = adaptive_stage1_cap(query, profile_cap=stage1_cap, high_fanout=False)
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

    def run_retrieve_phase() -> dict | None:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta
        if query_only and not _timed_out("retrieve"):
            orchestrator_hits = orchestrator_retrieve(repo_root, store, query, [], limit=stage1_cap)
            if not orchestrator_hits:
                phase_tracker.close_active()
                end_context_trace()
                return {
                    "error": "query-first retrieval found no candidates; rebuild the graph index",
                }

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
                    stage1_seen = {item["path"] for item in stage1_results}
                    additions = [g for g in generated if g["path"] not in stage1_seen]
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
        return None

    def run_hybrid_phase() -> None:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta

        # --- Phase 2: Hybrid signals ---
        if not skip_expensive_hybrid:
            all_files_set = set(store.all_files())
            if sparse_graph:
                all_files_set |= _all_repo_files(repo_root)
        if (
            not _timed_out("hybrid")
            and not skip_expensive_hybrid
            and not ablation_enabled("import")
        ):
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
        return None

    def run_semantic_phase() -> None:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta

        # Phase 2b: Semantic content matching (BM25 or TF-IDF fallback)
        if run_semantic_hybrid and not _timed_out("semantic") and not ablation_enabled("semantic"):
            kw_top_n = semantic_top_n(large_graph=large_graph, query_only=query_only)
            kw_results, semantic_meta = _semantic_search(
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
            kw_seen_paths = {r["path"] for r in results} | {h["path"] for h in hybrid_additions}
            for kw_path, kw_score in kw_results:
                if kw_path not in kw_seen_paths and kw_path not in files:
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
        return None

    def run_filter_phase() -> None:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta

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
        return None

    def run_rank_phase() -> None:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta

        # --- Phase 4: Query-aware ranking ---
        if not _timed_out("rank"):
            if feature_enabled("COMMUNITY_RANK") and files and not large_graph:
                use_leiden = feature_enabled("LEIDEN")
                if use_leiden:
                    comm_payload = detect_communities(
                        store, profile_name=profile_name, use_leiden=True
                    )
                    leiden_fallback = comm_payload.get("method") != "leiden"
                community_membership = community_membership_map(
                    store,
                    profile_name=profile_name,
                    use_leiden=use_leiden,
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
                if ablation_enabled("learned"):
                    learned = {}
                if query and not _timed_out("rank"):
                    embed_scores = query_embedding_scores(
                        repo_root, query, [r["path"] for r in filtered]
                    )
                    if ablation_enabled("embed"):
                        embed_scores = {}
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
                        if ablation_enabled("co_change"):
                            base_score = 1 + (term_hits * 10)
                        mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                        if not test_focused and _is_test_file(r["path"]):
                            base_score *= 0.05
                            if mirror_group.get("has_non_test"):
                                base_score *= 0.2
                        elif _is_noise_path(r["path"]):
                            base_score *= 0.05
                        elif mirror_group.get("has_test"):
                            base_score += 5
                        base_score = _apply_file_class_weight(base_score, r["path"], query_intent)
                        base_score *= _locality_multiplier(r["path"], files)
                        degree = _path_degree(r["path"])
                        hub_penalty = math.log2(2 + degree)
                        base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                        base_score += learned.get(r["path"], 0.0)
                        base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                        comm_boost = community_rank_boost(r["path"], files, community_membership)
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
                        if ablation_enabled("co_change"):
                            base_score = 1
                        mirror_group = mirror_groups.get("::".join(_mirror_key(r["path"])), {})
                        if _is_test_file(r["path"]):
                            base_score *= 0.05
                            if mirror_group.get("has_non_test"):
                                base_score *= 0.2
                        elif _is_noise_path(r["path"]):
                            base_score *= 0.05
                        elif mirror_group.get("has_test"):
                            base_score += 5
                        base_score = _apply_file_class_weight(base_score, r["path"], query_intent)
                        base_score *= _locality_multiplier(r["path"], files)
                        degree = _path_degree(r["path"])
                        hub_penalty = math.log2(2 + degree)
                        base_score = base_score / max(1.0, hub_penalty * hub_penalty_strength)
                        base_score += learned.get(r["path"], 0.0)
                        base_score += 0.15 * embed_scores.get(r["path"], 0.0)
                        comm_boost = community_rank_boost(r["path"], files, community_membership)
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
                    feats = apply_ablation_to_features(
                        _candidate_features(
                            r,
                            files=files,
                            node_degrees=node_degrees,
                            learned=learned,
                            embed_scores=embed_scores,
                            hub_penalty_strength=hub_penalty_strength,
                            already_have=already_have,
                            feedback_signals=feedback_signals,
                        )
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
        return None

    def run_pack_phase() -> dict:
        nonlocal \
            truncated, \
            timed_out_phase, \
            symbol_reads, \
            feedback_paths, \
            semantic_meta, \
            leiden_fallback, \
            sparse_graph, \
            query_only, \
            large_graph, \
            high_fanout, \
            max_seed_degree, \
            skip_expensive_hybrid, \
            run_semantic_hybrid, \
            stage1_cap, \
            expansion, \
            iterations, \
            max_depth, \
            files, \
            orchestrator_hits, \
            results, \
            hybrid_additions, \
            all_files_set, \
            filtered, \
            node_degrees, \
            learned, \
            embed_scores, \
            community_membership, \
            rank_diag_ctx, \
            context_files, \
            tokens_used, \
            included_paths, \
            budget_exhausted, \
            learned_tier1_meta, \
            summary_prune_meta

        def _refresh_pack_state() -> None:
            nonlocal tokens_used, included_paths, feedback_paths
            tokens_used = sum(
                int(entry.get("tokens_actual", count_json(tokenizer, entry)))
                for entry in context_files
            )
            included_paths = {entry["path"] for entry in context_files}
            feedback_paths = [p for p in feedback_paths if p in included_paths]

        # --- Phase 5+6: Incremental packing with honest tokenizer counts ---
        rank_diag_ctx = {
            "files": files,
            "node_degrees": node_degrees,
            "learned": learned,
            "embed_scores": embed_scores,
            "hub_penalty_strength": hub_penalty_strength,
        }
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
            elif tier == 1:
                entry["pick_reason"] = _compress_pick_reason(r, **rank_diag_ctx)
            tokens_used += entry_tokens
            context_files.append(entry)
            included_paths.add(r["path"])

            if query:
                feedback_paths.append(r["path"])

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

        confidence_fallbacks: dict[str, object] = {
            **semantic_meta,
            "leiden_fallback": leiden_fallback,
        }
        ablated = active_ablations()
        if ablated:
            confidence_fallbacks["ablations"] = ablated

        response["retrieval_confidence"] = build_retrieval_confidence(
            sparse_graph=sparse_graph,
            truncated=truncated,
            timed_out_phase=timed_out_phase or deadline.timed_out_phase,
            query_only=query_only,
            orchestrator_hit_count=len(orchestrator_hits),
            files_included=len(context_files),
            selective_hybrid=bool(large_graph and query_only and run_semantic_hybrid),
            fallbacks=confidence_fallbacks,
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

        return response

    phase_tracker = ContextPhaseTracker()
    begin_context_trace(
        request_id,
        query=(query[:120] if query else ""),
        profile=str(profile_name),
    )
    phase_tracker.enter("retrieve")
    early = run_retrieve_phase()
    if early is not None:
        return early
    phase_tracker.enter("hybrid")
    run_hybrid_phase()
    phase_tracker.enter("semantic")
    run_semantic_phase()
    phase_tracker.enter("filter")
    run_filter_phase()
    phase_tracker.enter("rank")
    run_rank_phase()
    phase_tracker.enter("pack")
    return run_pack_phase()
