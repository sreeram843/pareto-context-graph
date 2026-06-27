"""Unit tests for isolated context pipeline phases."""

from __future__ import annotations

from pathlib import Path

from pareto_context_graph.context_pipeline_phases import (
    PipelineCtx,
    init_pipeline_ctx,
    run_filter_phase,
)
from pareto_context_graph.deadlines import RequestDeadline
from pareto_context_graph.store import Store


def _minimal_ctx(
    repo: Path, *, results: list[dict], already_have: set[str] | None = None
) -> PipelineCtx:
    store = Store(repo)
    try:
        for row in results:
            store.upsert_file(row["path"])
        store.commit()
    finally:
        store.close()
    (repo / "src").mkdir(parents=True, exist_ok=True)
    for row in results:
        if row["path"] == "src/missing.py":
            continue
        target = repo / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# stub\n", encoding="utf-8")

    return init_pipeline_ctx(
        repo_root=repo,
        store=Store(repo),
        arguments={},
        request_id="test",
        seed_files=["src/seed.py"],
        query="handler",
        query_first=False,
        diagnostics=False,
        profile_name="small",
        profile={},
        files=["src/seed.py"],
        tokenizer=type("T", (), {"name": "estimate"})(),
        already_have=already_have or set(),
        session_merged=0,
        deadline=RequestDeadline(timeout_ms=5000),
        token_budget=4000,
        tier=1,
        min_weight=1,
        max_depth=2,
        stage1_cap=50,
        expansion="bfs",
        iterations=1,
        hub_penalty_strength=1.0,
        mmr_lambda=0.7,
        no_safety=True,
        compression="none",
    )


def test_run_filter_phase_drops_missing_and_already_have(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows = [
        {"path": "src/a.py", "depth": 1, "weight": 5, "signal": "co_change"},
        {"path": "src/missing.py", "depth": 1, "weight": 3, "signal": "co_change"},
        {"path": "src/seen.py", "depth": 1, "weight": 2, "signal": "co_change"},
    ]
    ctx = _minimal_ctx(repo, results=rows, already_have={"src/seen.py"})
    ctx.results = list(rows)

    run_filter_phase(ctx)

    paths = {row["path"] for row in ctx.filtered}
    assert paths == {"src/a.py"}
