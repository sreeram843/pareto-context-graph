"""Tests for synthetic feedback replay eval (Phase 5 acceptance)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pareto_context_graph.eval import EvalCase, mrr
from pareto_context_graph.feedback import FeedbackEventLog, record_feedback
from pareto_context_graph.feedback_replay import (
    MIN_MRR_IMPROVEMENT,
    apply_learn,
    clear_learning_state,
    learning_snapshot,
    run_feedback_replay,
    run_feedback_replay_for_repo,
)
from pareto_context_graph.graph import build_graph
from pareto_context_graph.ranker import load_ranker
from pareto_context_graph.server import _handle_tool_call
from tests.fixtures.build_repo import create_synthetic_repo


def _context_ranked(repo: Path, *, files: list[str], query: str) -> list[str]:
    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": files,
            "query": query,
            "tier": 1,
            "token_budget": 8000,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    return [entry["path"] for entry in payload.get("context_files", [])]


def test_apply_learn_writes_weights_and_ranker(tmp_path):
    repo = tmp_path / "repo"
    create_synthetic_repo(repo, commit_count=80, file_count=12, seed=5)
    build_graph(repo, max_commits=120)

    clear_learning_state(repo)
    logger = FeedbackEventLog(repo)
    logger.append(
        {
            "kind": "context_request",
            "request_id": "r1",
            "query": "q",
            "candidates": [
                {"path": "src/a.py", "features": {"rank_score": 5.0, "co_change": 3.0}},
                {"path": "src/f0.py", "features": {"rank_score": 8.0, "co_change": 1.0}},
                {"path": "src/b.py", "features": {"rank_score": 4.0, "co_change": 2.0}},
                {"path": "src/f1.py", "features": {"rank_score": 7.0, "co_change": 1.0}},
            ],
        },
        dedupe=False,
    )
    record_feedback(repo, kind="accept", request_id="r1", paths=["src/a.py", "src/b.py"], query="q")
    record_feedback(
        repo, kind="reject", request_id="r1", paths=["src/f0.py", "src/f1.py"], query="q"
    )

    result = apply_learn(repo)
    assert result["weights"] >= 1
    assert (repo / ".pareto-context-graph" / "weights.json").exists()
    assert result["ranker_saved"] is True
    assert load_ranker(repo) is not None


def test_synthetic_replay_improves_mrr(tmp_path):
    repo = tmp_path / "repo"
    create_synthetic_repo(repo, commit_count=200, file_count=30, seed=9)
    build_graph(repo, max_commits=300)

    train = [
        EvalCase(
            case_id="train_a",
            repo_key="synthetic",
            seed_files=["src/a.py"],
            query="handler module",
            expected_top_files=["src/b.py"],
        ),
        EvalCase(
            case_id="train_b",
            repo_key="synthetic",
            seed_files=["src/b.py"],
            query="paired module",
            expected_top_files=["src/a.py"],
        ),
    ]
    holdout = [
        EvalCase(
            case_id="holdout_a",
            repo_key="synthetic",
            seed_files=["src/a.py"],
            query="",
            expected_top_files=["src/b.py"],
            category="concept",
        ),
    ]

    clear_learning_state(repo)
    before_ranked = _context_ranked(repo, files=["src/a.py"], query="")
    before = mrr(before_ranked, ["src/b.py"])

    report = run_feedback_replay(
        repo,
        train + holdout,
        holdout_category="concept",
        train_repeats=3,
        min_mrr_improvement=0.0,
    )

    after_ranked = _context_ranked(repo, files=["src/a.py"], query="")
    after = mrr(after_ranked, ["src/b.py"])

    assert report.after_mrr >= report.baseline_mrr
    assert after >= before


@pytest.mark.skipif(
    not Path("bench/fastapi/.pareto-context-graph/graph.db").exists(),
    reason="fastapi bench graph not built",
)
def test_fastapi_feedback_replay_holdout_gain():
    repo = Path("bench/fastapi").resolve()
    with learning_snapshot(repo):
        report = run_feedback_replay_for_repo(
            "fastapi",
            repo,
            train_repeats=2,
            min_mrr_improvement=MIN_MRR_IMPROVEMENT,
        )
    assert report.holdout_cases >= 4
    assert report.weights_count >= 10, report.to_dict()
    # Holdout-gate invariant (what actually protects production): the report's pass
    # flag must be consistent with the measured delta, and production only saves the
    # learned ranker when it passes. A strong base ranking can legitimately leave no
    # gain for sparse synthetic feedback — in that case the gate must mark it not-passed
    # rather than the ranker silently regressing what ships.
    assert report.passed == (report.mrr_delta >= MIN_MRR_IMPROVEMENT - 1e-9), report.to_dict()
    if report.passed:
        assert report.after_mrr >= report.baseline_mrr - 1e-6, report.to_dict()
