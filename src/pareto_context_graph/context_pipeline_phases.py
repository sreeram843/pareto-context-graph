"""Context pipeline phase functions (testable, isolated)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ablation import ablation_enabled, active_ablations
from .adaptive_cap import adaptive_stage1_cap
from .audit import log_audit_event
from .blast import (
    blast_radius,
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
from .feedback import (
    FeedbackEventLog,
    feedback_log_enabled,
    feedback_path_signals,
    log_context_request,
)
from .hooks import redact_context_payload, run_post_context_hooks
from .knowledge_gap import build_knowledge_gap
from .metrics import METRICS, ContextPhaseTracker
from .orchestrator import retrieve as orchestrator_retrieve
from .payload_compress import apply_context_compression
from .pool import open_store
from .prune_learn import apply_learned_tier1_prune, learned_tier1_prune_enabled, load_prune_weights
from .ranker import apply_ranker_boost, blend_alpha, load_ranker
from .response_layers import apply_dual_layer_response
from .routing_hints import build_routing_hints
from .savings import build_context_savings
from .selective_hybrid import (
    allow_seed_hybrid,
    allow_semantic_hybrid,
    prefer_bm25_for_semantic,
    semantic_top_n,
)
from .session import record_session_paths, session_memory_enabled
from .spec_index import search_spec_context
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
from .tracing import end_context_trace


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
class PipelineCtx:
    repo_root: Path
    store: Store
    arguments: dict
    request_id: str
    seed_files: list[str]
    query: str
    query_first: bool
    diagnostics: bool
    profile_name: str
    profile: dict
    tokenizer: Any
    already_have: set[str]
    session_merged: int
    deadline: RequestDeadline
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
    files: list[str] = field(default_factory=list)
    truncated: bool = False
    symbol_reads: int = 0
    timed_out_phase: str = ""
    feedback_paths: list[str] = field(default_factory=list)
    semantic_meta: dict[str, object] = field(default_factory=dict)
    leiden_fallback: bool = False
    orchestrator_hits: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    hybrid_additions: list[dict[str, Any]] = field(default_factory=list)
    all_files_set: set[str] = field(default_factory=set)
    filtered: list[dict[str, Any]] = field(default_factory=list)
    node_degrees: dict[str, int] = field(default_factory=dict)
    learned: dict[str, float] = field(default_factory=dict)
    embed_scores: dict[str, float] = field(default_factory=dict)
    community_membership: dict[str, int] = field(default_factory=dict)
    rank_diag_ctx: dict[str, Any] = field(default_factory=dict)
    context_files: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    included_paths: set[str] = field(default_factory=set)
    budget_exhausted: bool = False
    learned_tier1_meta: dict = field(default_factory=dict)
    summary_prune_meta: dict = field(default_factory=dict)
    sparse_graph: bool = False
    query_only: bool = False
    large_graph: bool = False
    high_fanout: bool = False
    max_seed_degree: int = 0
    skip_expensive_hybrid: bool = False
    run_semantic_hybrid: bool = False

    @property
    def candidates(self) -> list[Candidate]:
        return [Candidate.from_dict(row) for row in self.results]


def init_pipeline_ctx(
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
) -> PipelineCtx:
    ctx = PipelineCtx(
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
        files=list(files),
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
    ctx.sparse_graph = store.edge_count() < 500
    ctx.query_only = not seed_files and bool(query) and query_first
    ctx.large_graph = store.file_count() >= LARGE_REPO_GRAPH_FILES
    if seed_files:
        ctx.max_seed_degree = max(store.file_degree(path) for path in seed_files)
        ctx.high_fanout = ctx.max_seed_degree >= HIGH_FANOUT_DEGREE_THRESHOLD
        if ctx.high_fanout:
            ctx.expansion = "bfs"
            ctx.iterations = 1
            ctx.max_depth = min(ctx.max_depth, 1)
            ctx.stage1_cap = adaptive_stage1_cap(
                query, profile_cap=ctx.stage1_cap, high_fanout=True
            )
        elif "stage1_cap" not in arguments:
            ctx.stage1_cap = adaptive_stage1_cap(
                query, profile_cap=ctx.stage1_cap, high_fanout=False
            )
    elif "stage1_cap" not in arguments:
        ctx.stage1_cap = adaptive_stage1_cap(query, profile_cap=ctx.stage1_cap, high_fanout=False)
    ctx.skip_expensive_hybrid = not allow_seed_hybrid(
        high_fanout=ctx.high_fanout,
        large_graph=ctx.large_graph,
    )
    ctx.run_semantic_hybrid = allow_semantic_hybrid(
        query=query,
        high_fanout=ctx.high_fanout,
        large_graph=ctx.large_graph,
        query_only=ctx.query_only,
    )
    return ctx


def pipeline_timed_out(ctx: PipelineCtx, phase: str) -> bool:
    if ctx.deadline.expired():
        ctx.truncated = True
        ctx.timed_out_phase = phase
        return True
    return False


def pipeline_symbols_for(ctx: PipelineCtx, path: Path) -> list[str]:
    if ctx.symbol_reads >= MAX_SYMBOL_FILE_READS:
        ctx.truncated = True
        return []
    ctx.symbol_reads += 1
    return extract_symbols(path)


# Pseudo-relevance co-change expansion (query-only path). The top lexical/semantic
# hits are treated as pseudo-relevant seeds and walked over the git co-change graph,
# pulling in coupled files that pure text search misses. Classic pseudo-relevance
# feedback (Rocchio/RM3) adapted to the structural graph that is this tool's core asset.
PRF_MAX_SEEDS = 3
PRF_MAX_ADDITIONS = 25


def prf_cochange_additions(ctx: PipelineCtx, existing_paths: set[str]) -> list[dict]:
    """Co-change neighbours of the top lexical hits (query-only pseudo-relevance feedback).

    Returns candidate dicts whose path is not in ``existing_paths``. Hub pseudo-seeds
    (degree >= high-fanout threshold) are skipped to avoid blow-up; the additions are
    capped and carry the ``cochange_prf`` signal so ranking/diagnostics stay legible.
    """
    if not feature_enabled("PRF_COCHANGE") or ablation_enabled("prf"):
        return []
    pseudo_seeds = [
        path
        for path in ctx.files[:PRF_MAX_SEEDS]
        if ctx.store.file_degree(path) < HIGH_FANOUT_DEGREE_THRESHOLD
    ]
    if not pseudo_seeds:
        return []
    neighbours = blast_radius(
        ctx.store,
        pseudo_seeds,
        min_weight=max(2, ctx.min_weight),
        max_depth=1,
        max_results=PRF_MAX_ADDITIONS,
        use_cache=True,
        expired_check=lambda: pipeline_timed_out(ctx, "retrieve"),
    )
    additions: list[dict] = []
    for n in neighbours:
        path = n["path"]
        if path in existing_paths or path in pseudo_seeds or _is_noise_path(path):
            continue
        additions.append(
            {
                "path": path,
                "depth": 1,
                "weight": int(n.get("weight", 1)),
                "source": "prf",
                "signal": "cochange_prf",
            }
        )
    return additions


def run_retrieve_phase(ctx: PipelineCtx, *, phase_tracker: ContextPhaseTracker) -> dict | None:
    if ctx.query_only and not pipeline_timed_out(ctx, "retrieve"):
        ctx.orchestrator_hits = orchestrator_retrieve(
            ctx.repo_root, ctx.store, ctx.query, [], limit=ctx.stage1_cap
        )
        if not ctx.orchestrator_hits:
            phase_tracker.close_active()
            end_context_trace()
            return {
                "error": "query-first retrieval found no candidates; rebuild the graph index",
            }

    # --- Phase 1: candidate generation (BFS/RWR, optionally iterative) ---
    stage1_results: list[dict] = []
    if not pipeline_timed_out(ctx, "retrieve"):
        if ctx.query_only:
            for hit in ctx.orchestrator_hits:
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
            ctx.files = [
                hit["path"] for hit in ctx.orchestrator_hits if not _is_noise_path(hit["path"])
            ][:3]
            if not pipeline_timed_out(ctx, "retrieve"):
                seen = {item["path"] for item in stage1_results}
                stage1_results.extend(prf_cochange_additions(ctx, seen))
        else:
            seed_pool = list(ctx.files)
            for round_idx in range(max(1, ctx.iterations)):
                if pipeline_timed_out(ctx, "retrieve"):
                    break
                generated = _stage1_candidates(
                    ctx.store,
                    seed_pool,
                    min_weight=ctx.min_weight,
                    max_depth=ctx.max_depth,
                    cap=ctx.stage1_cap,
                    expansion=ctx.expansion,
                    expired_check=lambda: pipeline_timed_out(ctx, "retrieve"),
                )
                stage1_seen = {item["path"] for item in stage1_results}
                additions = [g for g in generated if g["path"] not in stage1_seen]
                stage1_results.extend(additions)
                if not additions:
                    break
                seed_pool = [a["path"] for a in additions[: min(25, len(additions))]]

    ctx.results = stage1_results

    if not pipeline_timed_out(ctx, "retrieve") and ctx.orchestrator_hits and not ctx.query_only:
        seen_paths = {item["path"] for item in ctx.results}
        for hit in ctx.orchestrator_hits:
            path = hit["path"]
            if path in seen_paths:
                continue
            ctx.results.insert(
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


def run_hybrid_phase(ctx: PipelineCtx) -> None:

    # --- Phase 2: Hybrid signals ---
    if not ctx.skip_expensive_hybrid:
        ctx.all_files_set = set(ctx.store.all_files())
        if ctx.sparse_graph:
            ctx.all_files_set |= _all_repo_files(ctx.repo_root)
    if (
        not pipeline_timed_out(ctx, "hybrid")
        and not ctx.skip_expensive_hybrid
        and not ablation_enabled("import")
    ):
        for seed in ctx.files:
            if pipeline_timed_out(ctx, "hybrid"):
                break
            seed_path = ctx.repo_root / seed
            if seed_path.is_file():
                imports = extract_imports(seed_path)
                for ref_idx, ref in enumerate(imports):
                    if deadline_tick(ref_idx) and pipeline_timed_out(ctx, "hybrid"):
                        break
                    resolved = resolve_import_to_file(ref, ctx.all_files_set, from_path=seed)
                    if resolved and resolved not in {r["path"] for r in ctx.results}:
                        ctx.hybrid_additions.append(
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
                        if pipeline_timed_out(ctx, "hybrid"):
                            break
                        resolved = resolve_dbt_ref_to_file(ref_name, ctx.all_files_set)
                        if resolved and resolved not in {r["path"] for r in ctx.results}:
                            ctx.hybrid_additions.append(
                                {
                                    "path": resolved,
                                    "depth": 1,
                                    "weight": 3,
                                    "source": seed,
                                    "signal": "dbt_ref",
                                }
                            )

                    # Reverse dbt ref scan is O(repo); skip on large graphs.
                    if not ctx.large_graph:
                        seed_stem = seed_path.stem.lower()
                        for cand_idx, candidate in enumerate(ctx.all_files_set):
                            if deadline_tick(cand_idx) and pipeline_timed_out(ctx, "hybrid"):
                                break
                            if candidate == seed or not candidate.endswith(".sql"):
                                continue
                            cand_path = ctx.repo_root / candidate
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
                                if candidate not in {r["path"] for r in ctx.results}:
                                    ctx.hybrid_additions.append(
                                        {
                                            "path": candidate,
                                            "depth": 1,
                                            "weight": 3,
                                            "source": seed,
                                            "signal": "dbt_ref_reverse",
                                        }
                                    )

            if not ctx.skip_expensive_hybrid:
                pairs = find_naming_pairs(seed, ctx.all_files_set)
                for p in pairs:
                    if p not in {r["path"] for r in ctx.results}:
                        ctx.hybrid_additions.append(
                            {
                                "path": p,
                                "depth": 1,
                                "weight": 1,
                                "source": seed,
                                "signal": "naming",
                            }
                        )

                siblings = find_directory_siblings(seed, ctx.all_files_set)
                for sibling in siblings:
                    if sibling not in {r["path"] for r in ctx.results}:
                        sibling_weight = 2 if ctx.sparse_graph else 1
                        ctx.hybrid_additions.append(
                            {
                                "path": sibling,
                                "depth": 1,
                                "weight": sibling_weight,
                                "source": seed,
                                "signal": "directory",
                            }
                        )
    return None


def run_semantic_phase(ctx: PipelineCtx) -> None:

    # Phase 2b: Semantic content matching (BM25 or TF-IDF fallback)
    if (
        ctx.run_semantic_hybrid
        and not pipeline_timed_out(ctx, "semantic")
        and not ablation_enabled("semantic")
    ):
        kw_top_n = semantic_top_n(large_graph=ctx.large_graph, query_only=ctx.query_only)
        kw_results, ctx.semantic_meta = _semantic_search(
            ctx.repo_root,
            ctx.store,
            ctx.query,
            top_n=kw_top_n,
            prefer_bm25=prefer_bm25_for_semantic(
                large_graph=ctx.large_graph,
                query_only=ctx.query_only,
            )
            or ctx.store.has_search_index(),
            large_graph=ctx.large_graph and ctx.query_only,
        )
        kw_seen_paths = {r["path"] for r in ctx.results} | {h["path"] for h in ctx.hybrid_additions}
        for kw_path, kw_score in kw_results:
            if kw_path not in kw_seen_paths and kw_path not in ctx.files:
                ctx.hybrid_additions.append(
                    {
                        "path": kw_path,
                        "depth": 2,
                        "weight": max(1, int(kw_score / 10)),
                        "source": "keyword_match",
                        "signal": "semantic",
                    }
                )

    # Merge hybrid results
    seen = {r["path"] for r in ctx.results}
    for h in ctx.hybrid_additions:
        if h["path"] not in seen:
            ctx.results.append(h)
            seen.add(h["path"])
    return None


def run_filter_phase(ctx: PipelineCtx) -> None:

    # --- Phase 3: Filter existing + delta context ---
    existing_paths: list[str] = []
    if not pipeline_timed_out(ctx, "filter"):
        for idx, path in enumerate([r["path"] for r in ctx.results]):
            if pipeline_timed_out(ctx, "filter"):
                break
            if (ctx.repo_root / path).is_file():
                existing_paths.append(path)
    existing_set = set(existing_paths)
    ctx.filtered = [
        r
        for r in ctx.results
        if r["path"] in existing_set
        and (r["depth"] > 0 or r.get("signal") == "query_first")
        and r["path"] not in ctx.already_have
    ]
    return None


def run_rank_phase(ctx: PipelineCtx) -> None:

    # --- Phase 4: Query-aware ranking ---
    if not pipeline_timed_out(ctx, "rank"):
        if feature_enabled("COMMUNITY_RANK") and ctx.files and not ctx.large_graph:
            use_leiden = feature_enabled("LEIDEN")
            if use_leiden:
                comm_payload = detect_communities(
                    ctx.store, profile_name=ctx.profile_name, use_leiden=True
                )
                ctx.leiden_fallback = comm_payload.get("method") != "leiden"
            ctx.community_membership = community_membership_map(
                ctx.store,
                profile_name=ctx.profile_name,
                use_leiden=use_leiden,
            )
        if ctx.high_fanout:
            for idx, r in enumerate(ctx.filtered):
                if deadline_tick(idx) and pipeline_timed_out(ctx, "rank"):
                    break
                r["_relevance"] = float(r.get("weight", 1))
            ctx.filtered.sort(key=lambda x: -x.get("_relevance", 0))
        else:
            test_focused = _query_is_test_focused(ctx.query) if ctx.query else False
            query_intent = classify_query_intent(ctx.query) if ctx.query else "default"
            if ctx.large_graph:
                ctx.node_degrees = {}
            else:
                ctx.node_degrees = _node_degrees(ctx.repo_root, ctx.store)

            def _path_degree(path: str) -> int:
                if path not in ctx.node_degrees:
                    ctx.node_degrees[path] = ctx.store.file_degree(path)
                return ctx.node_degrees[path]

            ctx.learned = _learned_weights(ctx.repo_root)
            if ablation_enabled("learned"):
                ctx.learned = {}
            if ctx.query and not pipeline_timed_out(ctx, "rank"):
                ctx.embed_scores = query_embedding_scores(
                    ctx.repo_root, ctx.query, [r["path"] for r in ctx.filtered]
                )
                if ablation_enabled("embed"):
                    ctx.embed_scores = {}
            mirror_groups = _build_mirror_groups([r["path"] for r in ctx.filtered])
            if ctx.query:
                query_lower = ctx.query.lower()
                query_terms = set(query_lower.split())
                for idx, r in enumerate(ctx.filtered):
                    if deadline_tick(idx) and pipeline_timed_out(ctx, "rank"):
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
                    base_score *= _locality_multiplier(r["path"], ctx.files)
                    degree = _path_degree(r["path"])
                    hub_penalty = math.log2(2 + degree)
                    base_score = base_score / max(1.0, hub_penalty * ctx.hub_penalty_strength)
                    base_score += ctx.learned.get(r["path"], 0.0)
                    base_score += 0.15 * ctx.embed_scores.get(r["path"], 0.0)
                    comm_boost = community_rank_boost(
                        r["path"], ctx.files, ctx.community_membership
                    )
                    base_score += comm_boost
                    r["_community_boost"] = round(comm_boost, 4)
                    r["_symbols"] = pipeline_symbols_for(ctx, ctx.repo_root / r["path"])
                    r["_relevance"] = base_score
                ctx.filtered.sort(key=lambda x: -x.get("_relevance", x.get("weight", 0)))
            else:
                for idx, r in enumerate(ctx.filtered):
                    if deadline_tick(idx) and pipeline_timed_out(ctx, "rank"):
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
                    base_score *= _locality_multiplier(r["path"], ctx.files)
                    degree = _path_degree(r["path"])
                    hub_penalty = math.log2(2 + degree)
                    base_score = base_score / max(1.0, hub_penalty * ctx.hub_penalty_strength)
                    base_score += ctx.learned.get(r["path"], 0.0)
                    base_score += 0.15 * ctx.embed_scores.get(r["path"], 0.0)
                    comm_boost = community_rank_boost(
                        r["path"], ctx.files, ctx.community_membership
                    )
                    base_score += comm_boost
                    r["_community_boost"] = round(comm_boost, 4)
                    r["_symbols"] = pipeline_symbols_for(ctx, ctx.repo_root / r["path"])
                    r["_relevance"] = base_score
                ctx.filtered.sort(key=lambda x: -x.get("_relevance", 0))

            ranker = load_ranker(ctx.repo_root)
            feedback_alpha = blend_alpha(len(FeedbackEventLog(ctx.repo_root).read_all()))
            feedback_signals = feedback_path_signals(ctx.repo_root)
            for idx, r in enumerate(ctx.filtered):
                if deadline_tick(idx) and pipeline_timed_out(ctx, "rank"):
                    break
                feats = apply_ablation_to_features(
                    _candidate_features(
                        r,
                        files=ctx.files,
                        node_degrees=ctx.node_degrees,
                        learned=ctx.learned,
                        embed_scores=ctx.embed_scores,
                        hub_penalty_strength=ctx.hub_penalty_strength,
                        already_have=ctx.already_have,
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
                ctx.filtered.sort(key=lambda x: -x.get("_relevance", 0))
    else:
        for r in ctx.filtered:
            r["_relevance"] = r.get("weight", 1)

    # Apply diversity selection (MMR) over top candidates
    if ctx.high_fanout:
        ctx.filtered = ctx.filtered[: min(len(ctx.filtered), ctx.stage1_cap)]
    else:
        ctx.filtered = _mmr_select(
            ctx.filtered,
            limit=min(len(ctx.filtered), ctx.stage1_cap),
            mmr_lambda=ctx.mmr_lambda,
            expired_check=lambda: pipeline_timed_out(ctx, "rank"),
        )
    return None


def run_pack_phase(ctx: PipelineCtx, *, phase_tracker: ContextPhaseTracker) -> dict:

    def _refresh_pack_state() -> None:
        ctx.tokens_used = sum(
            int(entry.get("tokens_actual", count_json(ctx.tokenizer, entry)))
            for entry in ctx.context_files
        )
        ctx.included_paths = {entry["path"] for entry in ctx.context_files}
        ctx.feedback_paths = [p for p in ctx.feedback_paths if p in ctx.included_paths]

    # --- Phase 5+6: Incremental packing with honest tokenizer counts ---
    ctx.rank_diag_ctx = {
        "files": ctx.files,
        "node_degrees": ctx.node_degrees,
        "learned": ctx.learned,
        "embed_scores": ctx.embed_scores,
        "hub_penalty_strength": ctx.hub_penalty_strength,
    }
    max_pack_files = 25 if ctx.high_fanout else len(ctx.filtered)
    for pack_idx, r in enumerate(ctx.filtered):
        if pack_idx >= max_pack_files:
            break
        if pipeline_timed_out(ctx, "pack"):
            break
        fp = ctx.repo_root / r["path"]
        if not fp.is_file():
            continue
        r["tokens"] = estimate_file_tokens(fp)

        entry = _build_context_entry(
            r, ctx.repo_root, ctx.tier, ctx.query, ctx.files, ctx.compression
        )
        if not ctx.no_safety:
            _redact_entry_fields(entry)

        entry_tokens = count_json(ctx.tokenizer, entry)
        if ctx.tokens_used + entry_tokens > ctx.token_budget:
            ctx.budget_exhausted = True
            break

        entry["tokens_actual"] = entry_tokens
        if ctx.diagnostics:
            entry["diagnostics"] = _entry_diagnostics(r, **ctx.rank_diag_ctx)
        elif ctx.tier == 1:
            entry["pick_reason"] = _compress_pick_reason(r, **ctx.rank_diag_ctx)
        ctx.tokens_used += entry_tokens
        ctx.context_files.append(entry)
        ctx.included_paths.add(r["path"])

        if ctx.query:
            ctx.feedback_paths.append(r["path"])

    if ctx.tier == 1:
        tier1_active = (
            bool(ctx.arguments["learned_tier1_prune"])
            if "learned_tier1_prune" in ctx.arguments
            else learned_tier1_prune_enabled(ctx.repo_root)
        )
        if tier1_active:
            prune_weights = load_prune_weights(ctx.repo_root)
            if prune_weights:
                ctx.context_files, ctx.learned_tier1_meta = apply_learned_tier1_prune(
                    ctx.context_files,
                    tier=ctx.tier,
                    prune_weights=prune_weights,
                    seed_files=ctx.files,
                )
                if ctx.learned_tier1_meta.get("dropped_count", 0) > 0:
                    _refresh_pack_state()
    if (
        ctx.tier == 1
        and ctx.query
        and request_flag(ctx.arguments, "summary_prune", "SUMMARY_PRUNE")
    ):
        ctx.context_files, ctx.summary_prune_meta = apply_summary_prune(
            ctx.context_files,
            query=ctx.query,
            tier=ctx.tier,
            seed_files=ctx.files,
        )
        if ctx.summary_prune_meta.get("dropped_count", 0) > 0:
            _refresh_pack_state()

    dropped_paths = [
        r["path"]
        for r in ctx.filtered
        if r["path"] not in ctx.included_paths and (ctx.repo_root / r["path"]).is_file()
    ]

    dropped_lookup = {r["path"]: r for r in ctx.filtered}
    counterfactual_candidates = [
        {
            "path": r["path"],
            "score": float(r.get("_relevance", r.get("weight", 0))),
            "features": dict(r.get("_features") or {}),
            "included": r["path"] in ctx.included_paths,
        }
        for r in ctx.filtered
    ]
    for r in ctx.filtered:
        r.pop("_relevance", None)
        r.pop("_entry_tokens", None)
        r.pop("_symbols", None)
        r.pop("_features", None)
        r.pop("_orchestrator_score", None)

    response: dict = {
        "response_version": 3,
        "request_id": ctx.request_id,
        "seed_files": ctx.seed_files if ctx.seed_files else ctx.files,
        "context_files": ctx.context_files,
        "tier": ctx.tier,
        "profile": ctx.profile_name,
        "tokenizer": ctx.tokenizer.name,
        "compression": ctx.compression,
        "tokens_used": ctx.tokens_used,
        "token_budget": ctx.token_budget,
        "files_included": len(ctx.context_files),
        "files_available": len(ctx.filtered),
        "iterations": ctx.iterations,
        "expansion": ctx.expansion,
    }
    if not ctx.seed_files and ctx.query_first:
        response["query_first"] = True
    if ctx.large_graph and ctx.query_only and ctx.run_semantic_hybrid:
        response["selective_hybrid"] = True
    if ctx.diagnostics:
        response["diagnostics"] = True
    if dropped_paths:
        dropped_payload: dict = {
            "count": len(dropped_paths),
            "paths": dropped_paths[:10],
            "reason": "budget_exhausted" if ctx.budget_exhausted else "unavailable",
        }
        if ctx.diagnostics:
            dropped_payload["details"] = [
                {
                    "path": path,
                    "reason": dropped_payload["reason"],
                    "diagnostics": _entry_diagnostics(
                        dropped_lookup.get(path, {"path": path}),
                        **ctx.rank_diag_ctx,
                    ),
                }
                for path in dropped_paths[:10]
            ]
        response["dropped_candidates"] = dropped_payload
    if ctx.truncated:
        response["truncated"] = True
        response["truncated_phase"] = ctx.timed_out_phase or ctx.deadline.timed_out_phase
    if ctx.already_have:
        response["skipped_already_have"] = len(ctx.already_have)
    if ctx.session_merged:
        response["session_already_have"] = ctx.session_merged
    if ctx.summary_prune_meta:
        response["summary_prune"] = ctx.summary_prune_meta
    if ctx.learned_tier1_meta:
        response["learned_tier1_prune"] = ctx.learned_tier1_meta
    if "stage1_cap" not in ctx.arguments:
        response["stage1_cap"] = ctx.stage1_cap

    if not ctx.no_safety:
        response = redact_context_payload(response)
    response = run_post_context_hooks(ctx.repo_root, response)
    for entry in response["context_files"]:
        entry["tokens_actual"] = count_json(ctx.tokenizer, entry)
        ctx.tokens_used += entry["tokens_actual"]
    response["tokens_used"] = ctx.tokens_used
    if not ctx.truncated and not ctx.large_graph:
        response["context_savings"] = build_context_savings(
            ctx.repo_root,
            graph_tokens=ctx.tokens_used,
            tokenizer=ctx.tokenizer.name,
            query=ctx.query,
            seed_files=ctx.seed_files,
        )

    response = apply_context_compression(
        response,
        repo_root=ctx.repo_root,
        query=ctx.query,
        compression=ctx.compression,
        tokenizer=ctx.tokenizer,
    )
    if response.get("context_savings") and response.get("tokens_before_compress"):
        response["context_savings"]["graph_tokens"] = int(response["tokens_used"])

    suggested = build_suggested_next(
        tier=ctx.tier,
        context_files=response["context_files"],
        compression=ctx.compression,
        truncated=ctx.truncated,
        timed_out_phase=ctx.timed_out_phase or ctx.deadline.timed_out_phase,
    )
    if suggested:
        response["suggested_next"] = suggested

    confidence_fallbacks: dict[str, object] = {
        **ctx.semantic_meta,
        "leiden_fallback": ctx.leiden_fallback,
    }
    ablated = active_ablations()
    if ablated:
        confidence_fallbacks["ablations"] = ablated

    response["retrieval_confidence"] = build_retrieval_confidence(
        sparse_graph=ctx.sparse_graph,
        truncated=ctx.truncated,
        timed_out_phase=ctx.timed_out_phase or ctx.deadline.timed_out_phase,
        query_only=ctx.query_only,
        orchestrator_hit_count=len(ctx.orchestrator_hits),
        files_included=len(ctx.context_files),
        selective_hybrid=bool(ctx.large_graph and ctx.query_only and ctx.run_semantic_hybrid),
        fallbacks=confidence_fallbacks,
    )

    knowledge_gap = build_knowledge_gap(
        confidence=response["retrieval_confidence"],
        query_only=ctx.query_only,
        orchestrator_hit_count=len(ctx.orchestrator_hits),
        files_included=len(response["context_files"]),
        seed_files=ctx.seed_files,
    )
    if knowledge_gap:
        response["knowledge_gap"] = knowledge_gap

    routing_hints = build_routing_hints(
        ctx.repo_root,
        query=ctx.query,
        returned_paths=[str(e.get("path", "")) for e in response["context_files"]],
        seed_paths=ctx.seed_files,
        hub_degrees=ctx.node_degrees,
    )
    if routing_hints:
        response["routing_hints"] = routing_hints

    include_specs = bool(ctx.arguments.get("include_specs", False))
    if include_specs and ctx.query.strip():
        spec_limit = int(ctx.arguments.get("spec_limit", 5))
        spec_hits = search_spec_context(
            ctx.store, ctx.repo_root, ctx.query, limit=max(1, min(spec_limit, 10))
        )
        if spec_hits:
            response["spec_context"] = spec_hits

    if feedback_log_enabled(ctx.arguments):
        log_context_request(
            ctx.repo_root,
            request_id=ctx.request_id,
            query=ctx.query,
            seed_files=ctx.seed_files if ctx.seed_files else ctx.files,
            candidates=counterfactual_candidates,
            returned_paths=[entry.get("path", "") for entry in response["context_files"]],
        )

        if ctx.feedback_paths:
            with open_store(ctx.repo_root, write=True) as wstore:
                for path in ctx.feedback_paths:
                    wstore.log_feedback(query=ctx.query, file_path=path, returned=True, used=False)

    log_audit_event(
        ctx.repo_root,
        command="context",
        query=ctx.query,
        returned_paths=len(response["context_files"]),
        tokens_used=int(response["tokens_used"]),
        truncated=ctx.truncated,
        request_id=ctx.request_id,
    )
    METRICS.inc("cgmcp_feedback_events_total", kind="context")
    METRICS.inc("cgmcp_context_requests_total")
    phase_tracker.close_active()
    end_context_trace()

    if session_memory_enabled(ctx.arguments):
        record_session_paths(
            ctx.repo_root,
            [entry.get("path", "") for entry in response["context_files"]],
        )

    return apply_dual_layer_response(response)
