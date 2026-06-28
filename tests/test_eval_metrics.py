"""Unit tests for eval metrics (synthetic, deterministic)."""

from pathlib import Path

from pareto_context_graph.eval import (
    agent_grep_top_files,
    aggregate_results,
    budget_honesty,
    check_grep_counterfactual_gate,
    compare_to_baseline,
    mrr,
    ndcg_at_k,
    portable_eval_payload,
    recall_at_k,
    token_efficiency,
)


def test_candidate_pool_recall():
    from pareto_context_graph.eval import candidate_pool_recall

    pool = ["a.py", "b.py", "c.py"]
    assert candidate_pool_recall(pool, ["a.py", "x.py"]) == 0.5
    assert candidate_pool_recall(pool, ["a.py", "b.py", "c.py"]) == 1.0


def test_recall_at_k():
    ranked = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    expected = ["b.py", "z.py"]
    assert recall_at_k(ranked, expected, 5) == 0.5
    assert recall_at_k(ranked, expected, 1) == 0.0
    assert recall_at_k(ranked, [], 5) == 1.0


def test_format_ablation_table():
    from pareto_context_graph.eval import format_ablation_table

    table = format_ablation_table(
        {
            "baseline": {
                "recall_at_5": 0.7,
                "candidate_pool_recall": 0.9,
                "pre_mmr_recall_at_5": 0.72,
            },
            "ablations": [
                {
                    "signal": "prf",
                    "recall_at_5": 0.71,
                    "candidate_pool_recall": 0.9,
                    "pre_mmr_recall_at_5": 0.73,
                    "delta": 0.01,
                }
            ],
        }
    )
    assert "prf" in table
    assert "0.7100" in table


def test_mrr():
    ranked = ["x.py", "y.py", "target.py"]
    assert mrr(ranked, ["target.py"]) == 1 / 3
    assert mrr(ranked, ["missing.py"]) == 0.0


def test_ndcg_at_k():
    ranked = ["b.py", "a.py", "c.py"]
    expected = ["a.py", "b.py"]
    score = ndcg_at_k(ranked, expected, 10)
    assert 0.0 < score <= 1.0


def test_token_efficiency_and_budget_honesty():
    assert token_efficiency(100, 2) == 0.02
    assert token_efficiency(0, 2) == 0.0
    assert budget_honesty(50, 100) == 1.0
    assert budget_honesty(150, 100) == 0.5


def test_aggregate_results_empty():
    summary = aggregate_results([])
    assert summary["cases"] == 0
    assert summary["mean_recall_at_5"] == 0.0


def test_aggregate_results_mean():
    results = [
        {
            "recall_at_5": 1.0,
            "mrr": 1.0,
            "ndcg_at_10": 1.0,
            "tokens_used": 100,
            "token_efficiency": 0.1,
            "budget_honesty": 1.0,
            "payload_honesty": 1.0,
            "reduction_vs_corpus": 10.0,
            "reduction_vs_agent": 2.0,
        },
        {
            "recall_at_5": 0.5,
            "mrr": 0.5,
            "ndcg_at_10": 0.5,
            "tokens_used": 200,
            "token_efficiency": 0.05,
            "budget_honesty": 0.8,
            "payload_honesty": 1.0,
            "reduction_vs_corpus": 5.0,
            "reduction_vs_agent": 1.0,
        },
    ]
    summary = aggregate_results(results)
    assert summary["cases"] == 2
    assert summary["mean_recall_at_5"] == 0.75
    assert summary["mean_mrr"] == 0.75
    assert summary["mean_tokens_used"] == 150.0


def test_compare_to_baseline_pass_and_fail():
    baseline = {
        "summary": {"cases": 2, "mean_recall_at_5": 0.8, "mean_mrr": 0.7, "mean_ndcg_at_10": 0.6},
        "results": [
            {"case_id": "a", "recall_at_5": 0.8, "mrr": 0.7, "ndcg_at_10": 0.6},
            {"case_id": "b", "recall_at_5": 0.8, "mrr": 0.7, "ndcg_at_10": 0.6},
        ],
    }
    improved = {
        "summary": {
            "cases": 2,
            "mean_recall_at_5": 0.85,
            "mean_mrr": 0.75,
            "mean_ndcg_at_10": 0.65,
        },
        "results": [
            {"case_id": "a", "recall_at_5": 0.85, "mrr": 0.75, "ndcg_at_10": 0.65},
            {"case_id": "b", "recall_at_5": 0.85, "mrr": 0.75, "ndcg_at_10": 0.65},
        ],
    }
    regressed = {
        "summary": {"cases": 2, "mean_recall_at_5": 0.5, "mean_mrr": 0.7, "mean_ndcg_at_10": 0.6},
        "results": [
            {"case_id": "a", "recall_at_5": 0.5, "mrr": 0.7, "ndcg_at_10": 0.6},
            {"case_id": "b", "recall_at_5": 0.5, "mrr": 0.7, "ndcg_at_10": 0.6},
        ],
    }

    assert compare_to_baseline(improved, baseline)["passed"] is True
    report = compare_to_baseline(regressed, baseline, threshold=0.02)
    assert report["passed"] is False
    assert report["regressions"][0]["metric"] == "mean_recall_at_5"


def test_agent_grep_top_files_on_fixture_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("def login():\n    return authenticate_user()\n")
    (repo / "user.py").write_text("def authenticate_user():\n    pass\n")
    (repo / "readme.md").write_text("no match here\n")

    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test User",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    hits = agent_grep_top_files(repo, "authenticate user login")
    assert "auth.py" in hits or "user.py" in hits


def test_check_grep_counterfactual_gate_pass_and_fail():
    passing = [
        {
            "case_id": "better_recall",
            "recall_at_5": 1.0,
            "tokens_used": 5000,
            "agent_baseline_tokens": 1000,
            "reduction_vs_agent": 0.2,
            "agent_recall_at_5": 0.5,
            "agent_baseline_paths": ["a.py"],
            "expected_top_files": ["a.py", "b.py"],
        },
        {
            "case_id": "better_tokens",
            "recall_at_5": 0.5,
            "tokens_used": 100,
            "agent_baseline_tokens": 500,
            "reduction_vs_agent": 5.0,
            "agent_recall_at_5": 0.8,
            "agent_baseline_paths": ["x.py"],
            "expected_top_files": ["x.py", "y.py"],
        },
        {
            "case_id": "no_grep",
            "recall_at_5": 0.0,
            "tokens_used": 50,
            "agent_baseline_tokens": 0,
            "reduction_vs_agent": 0.0,
            "agent_baseline_paths": [],
            "expected_top_files": ["z.py"],
        },
    ]
    assert check_grep_counterfactual_gate(passing)["passed"] is True

    failing = [
        {
            "case_id": "grep_wins_both",
            "recall_at_5": 0.2,
            "tokens_used": 900,
            "agent_baseline_tokens": 300,
            "reduction_vs_agent": 0.33,
            "agent_recall_at_5": 0.8,
            "agent_baseline_paths": ["a.py", "b.py"],
            "expected_top_files": ["a.py", "b.py", "c.py"],
        },
    ]
    report = check_grep_counterfactual_gate(failing)
    assert report["passed"] is False
    assert report["failures"][0]["case_id"] == "grep_wins_both"


def test_portable_eval_payload_rewrites_absolute_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {
        "golden_dir": str(tmp_path / "tests/eval/golden"),
        "repos": {"fastapi": str(tmp_path / "bench/fastapi")},
        "results": [{"case_id": "x", "repo_root": str(tmp_path / "bench/fastapi")}],
    }
    out = portable_eval_payload(payload, base=tmp_path)
    assert out["golden_dir"] == "tests/eval/golden"
    assert out["repos"]["fastapi"] == "bench/fastapi"
    assert out["results"][0]["repo_root"] == "bench/fastapi"
