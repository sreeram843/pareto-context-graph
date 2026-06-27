"""Week 5: init/sync aliases, agent A/B harness, cross-file coverage in doctor."""

from __future__ import annotations

import argparse
import json

from pareto_context_graph.agent_ab import (
    check_agent_ab_gate,
    run_agent_ab_study,
    summarize_agent_ab,
)
from pareto_context_graph.build_estimate import gather_doctor_report
from pareto_context_graph.cli import cmd_init, cmd_sync
from pareto_context_graph.doctor import format_doctor_text
from pareto_context_graph.graph import build_graph_sharded
from pareto_context_graph.store import Store


def test_init_builds_graph(tmp_path, synthetic_repo_factory, capsys):
    repo = synthetic_repo_factory(commits=40, files=8, seed=51)
    args = argparse.Namespace(
        repo=str(repo),
        commits=60,
        since=None,
        shards=1,
        profile="tiny",
        from_snapshot=None,
        with_search_index=False,
        skip_install=True,
        target="auto",
        location="local",
        watch=False,
    )
    cmd_init(args)
    out = capsys.readouterr().out
    assert "Building graph" in out
    assert "Next steps" in out
    assert "doctor" in out
    store = Store(repo)
    try:
        assert store.file_count() > 0
    finally:
        store.close()


def test_sync_incremental_noop(synthetic_repo_factory, capsys):
    repo = synthetic_repo_factory(commits=40, files=8, seed=52)
    store = build_graph_sharded(repo, max_commits=60, shards=1, profile_name="tiny")
    store.close()

    args = argparse.Namespace(repo=str(repo), profile="tiny", with_index=False)
    cmd_sync(args)
    out = capsys.readouterr().out
    assert "Synced graph" in out
    assert "Files:" in out


def test_summarize_agent_ab():
    rows = [
        {
            "arm": "pcg",
            "tool_calls": 1,
            "file_reads": 5,
            "grep_calls": 0,
            "tokens": 1000,
            "wall_time_ms": 50,
            "recall_at_5": 0.8,
        },
        {
            "arm": "baseline",
            "tool_calls": 4,
            "file_reads": 3,
            "grep_calls": 1,
            "tokens": 5000,
            "wall_time_ms": 120,
            "recall_at_5": 0.5,
        },
    ]
    summary = summarize_agent_ab(rows)
    assert summary["pcg"]["tool_calls"] == 1.0
    assert summary["baseline"]["grep_calls"] == 1.0
    assert summary["pcg_vs_baseline"]["tool_calls_reduction_pct"] == 75.0
    assert summary["pcg_vs_baseline"]["recall_at_5_delta"] == 0.3


def test_check_agent_ab_gate_pass_and_fail():
    baseline = {"summary": {"pcg": {"recall_at_5": 0.8}, "pcg_vs_baseline": {}}}
    good = {
        "summary": {
            "pcg": {"recall_at_5": 0.82},
            "pcg_vs_baseline": {"recall_at_5_delta": 0.05, "tool_calls_reduction_pct": 50.0},
        }
    }
    assert check_agent_ab_gate(good, baseline)["passed"] is True

    bad = {
        "summary": {
            "pcg": {"recall_at_5": 0.5},
            "pcg_vs_baseline": {"recall_at_5_delta": -0.1, "tool_calls_reduction_pct": -5.0},
        }
    }
    gate = check_agent_ab_gate(bad, baseline)
    assert gate["passed"] is False
    assert len(gate["failures"]) >= 2


def test_agent_ab_study_on_synthetic_case(tmp_path, synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=8, seed=53)
    store = build_graph_sharded(repo, max_commits=70, shards=1, profile_name="tiny")
    store.close()

    golden = tmp_path / "golden"
    repo_key = "demo"
    case_dir = golden / repo_key
    case_dir.mkdir(parents=True)
    (case_dir / "cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "demo_seed",
                        "repo_key": repo_key,
                        "seed_files": ["src/f0.py"],
                        "query": "",
                        "expected_top_files": ["src/f0.py", "src/f1.py"],
                        "tier": 1,
                        "token_budget": 50000,
                    }
                ]
            }
        )
    )

    result = run_agent_ab_study({repo_key: repo}, golden)
    assert result["cases"] == 1
    assert len(result["results"]) == 2
    assert result["summary"]["pcg"]["tool_calls"] == 1.0
    assert result["summary"]["baseline"]["grep_calls"] >= 0.0


def test_doctor_shows_cross_file_coverage(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=54)
    store = build_graph_sharded(repo, max_commits=80, shards=1, profile_name="tiny")
    store.close()

    report = gather_doctor_report(repo)
    assert "connected_files" in report
    assert "cross_file_coverage_pct" in report
    assert report["connected_files"] > 0
    text = format_doctor_text(report)
    assert "Cross-file:" in text


def test_stats_includes_cross_file_coverage(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=55)
    store = build_graph_sharded(repo, max_commits=80, shards=1, profile_name="tiny")
    stats = store.graph_stats()
    store.close()
    assert stats["cross_file_coverage_pct"] > 0
    assert stats["connected_files"] > 0
