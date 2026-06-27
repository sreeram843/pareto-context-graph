"""Context pipeline — phased retrieve → rank → pack."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context_pipeline_phases import (
    Candidate,
    PipelineCtx,
    apply_ablation_to_features,
    init_pipeline_ctx,
    run_filter_phase,
    run_hybrid_phase,
    run_pack_phase,
    run_rank_phase,
    run_retrieve_phase,
    run_semantic_phase,
)
from .deadlines import RequestDeadline
from .metrics import ContextPhaseTracker
from .store import Store
from .tracing import begin_context_trace

__all__ = [
    "Candidate",
    "ContextPipelineState",
    "PipelineConfig",
    "PipelineCtx",
    "PHASES",
    "apply_ablation_to_features",
    "execute_context_pipeline",
    "init_pipeline_ctx",
    "run_filter_phase",
    "run_hybrid_phase",
    "run_pack_phase",
    "run_rank_phase",
    "run_retrieve_phase",
    "run_semantic_phase",
]


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
    ctx = init_pipeline_ctx(
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
    phase_tracker = ContextPhaseTracker()
    begin_context_trace(
        request_id,
        query=(query[:120] if query else ""),
        profile=str(profile_name),
    )
    phase_tracker.enter("retrieve")
    early = run_retrieve_phase(ctx, phase_tracker=phase_tracker)
    if early is not None:
        return early
    phase_tracker.enter("hybrid")
    run_hybrid_phase(ctx)
    phase_tracker.enter("semantic")
    run_semantic_phase(ctx)
    phase_tracker.enter("filter")
    run_filter_phase(ctx)
    phase_tracker.enter("rank")
    run_rank_phase(ctx)
    phase_tracker.enter("pack")
    return run_pack_phase(ctx, phase_tracker=phase_tracker)
