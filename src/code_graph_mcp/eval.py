"""Evaluation harness for retrieval quality regressions.

Runs the real ``code_graph`` context command against curated cases and reports
retrieval metrics so ranking changes can be validated before shipping.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from .server import _handle_tool_call
from .store import Store


DEFAULT_CASES_PATH = Path("tests/eval/golden")


@dataclass
class EvalCase:
    """A single retrieval-evaluation scenario."""

    case_id: str
    repo_key: str
    seed_files: list[str]
    query: str
    expected_top_files: list[str]
    tier: int = 1
    token_budget: int = 50000
    max_depth: int = 2
    min_weight: int = 2
    notes: str = ""


def load_cases_for_repo(repo_key: str, golden_dir: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    """Load evaluation cases from per-repo golden directory."""
    cases_path = golden_dir / repo_key / "cases.json"
    if not cases_path.exists():
        raise RuntimeError(
            f"No cases found for repo {repo_key!r} at {cases_path}. "
            f"Create {cases_path} with case definitions."
        )
    payload = json.loads(cases_path.read_text())
    return [
        EvalCase(
            case_id=item["case_id"],
            repo_key=item.get("repo_key", repo_key),
            seed_files=item["seed_files"],
            query=item["query"],
            expected_top_files=item["expected_top_files"],
            tier=item.get("tier", 1),
            token_budget=item.get("token_budget", 50000),
            max_depth=item.get("max_depth", 2),
            min_weight=item.get("min_weight", 2),
            notes=item.get("notes", ""),
        )
        for item in payload["cases"]
    ]


def parse_repo_overrides(repo_args: list[str]) -> dict[str, Path]:
    """Parse repo overrides supplied as KEY=/abs/path."""
    overrides: dict[str, Path] = {}
    for item in repo_args:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise ValueError(f"Invalid repo override: {item!r}; use key=/abs/path")
        overrides[key.strip()] = Path(value.strip()).expanduser().resolve()
    return overrides


def _recall_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 1.0
    hits = len(set(ranked[:k]) & set(expected))
    return hits / len(expected)


def _mrr(ranked: list[str], expected: list[str]) -> float:
    expected_set = set(expected)
    for index, path in enumerate(ranked, start=1):
        if path in expected_set:
            return 1.0 / index
    return 0.0


def _ndcg_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 1.0

    relevance = {path: len(expected) - idx for idx, path in enumerate(expected)}

    def dcg(paths: list[str]) -> float:
        score = 0.0
        for index, path in enumerate(paths[:k], start=1):
            rel = relevance.get(path, 0)
            if rel:
                score += rel / math.log2(index + 1)
        return score

    ideal = dcg(expected)
    if ideal == 0:
        return 0.0
    return dcg(ranked) / ideal


def _token_efficiency(tokens_used: int, expected_files_count: int) -> float:
    """Efficiency score: tokens_used per expected file retrieved.
    
    High is better (fewer tokens per file).
    If no expected files, return 1.0 (perfect).
    """
    if expected_files_count == 0:
        return 1.0
    if tokens_used == 0:
        return 0.0
    return round(expected_files_count / tokens_used, 4)


def _budget_honesty(tokens_used: int, token_budget: int) -> float:
    """Budget honesty: 1.0 if within budget, penalized if exceeded.
    
    score = 1.0 - |tokens_used - token_budget| / token_budget
    Clamped to [0, 1].
    """
    if tokens_used <= token_budget:
        return 1.0
    if token_budget == 0:
        return 0.0
    overshoot_ratio = (tokens_used - token_budget) / token_budget
    return round(max(0.0, 1.0 - overshoot_ratio), 4)


def _run_case(case: EvalCase, repo_root: Path) -> dict:
    store = Store(repo_root)
    try:
        if store.file_count() == 0:
            raise RuntimeError(
                f"Repo {repo_root} has no graph built yet. Run build first."
            )
    finally:
        store.close()

    raw = _handle_tool_call(
        repo_root,
        "code_graph",
        {
            "command": "context",
            "files": case.seed_files,
            "query": case.query,
            "tier": case.tier,
            "token_budget": case.token_budget,
            "max_depth": case.max_depth,
            "min_weight": case.min_weight,
        },
    )
    response = json.loads(raw)
    ranked = [entry["path"] for entry in response.get("context_files", [])]
    tokens_used = response.get("tokens_used", 0)
    expected_hits = len(set(ranked) & set(case.expected_top_files))
    return {
        "case_id": case.case_id,
        "repo_key": case.repo_key,
        "repo_root": str(repo_root),
        "seed_files": case.seed_files,
        "query": case.query,
        "expected_top_files": case.expected_top_files,
        "returned_paths": ranked,
        "returned_count": len(ranked),
        "files_available": response.get("files_available", 0),
        "files_included": response.get("files_included", 0),
        "tokens_used": tokens_used,
        "token_budget": case.token_budget,
        "recall_at_5": round(_recall_at_k(ranked, case.expected_top_files, 5), 4),
        "mrr": round(_mrr(ranked, case.expected_top_files), 4),
        "ndcg_at_10": round(_ndcg_at_k(ranked, case.expected_top_files, 10), 4),
        "token_efficiency": _token_efficiency(tokens_used, expected_hits),
        "budget_honesty": _budget_honesty(tokens_used, case.token_budget),
        "notes": case.notes,
    }


def _aggregate(results: list[dict]) -> dict:
    count = len(results)
    if count == 0:
        return {
            "cases": 0,
            "mean_recall_at_5": 0.0,
            "mean_mrr": 0.0,
            "mean_ndcg_at_10": 0.0,
            "mean_token_efficiency": 0.0,
            "mean_budget_honesty": 0.0,
        }

    return {
        "cases": count,
        "mean_recall_at_5": round(sum(r["recall_at_5"] for r in results) / count, 4),
        "mean_mrr": round(sum(r["mrr"] for r in results) / count, 4),
        "mean_ndcg_at_10": round(sum(r["ndcg_at_10"] for r in results) / count, 4),
        "mean_tokens_used": round(sum(r["tokens_used"] for r in results) / count, 2),
        "mean_token_efficiency": round(sum(r["token_efficiency"] for r in results) / count, 4),
        "mean_budget_honesty": round(sum(r["budget_honesty"] for r in results) / count, 4)
    }

    by_repo = {}
    for result in results:
        repo_key = result["repo_key"]
        if repo_key not in by_repo:
            by_repo[repo_key] = []
        by_repo[repo_key].append(result)

    for repo_key, repo_results in by_repo.items():
        repo_dir = golden_dir / repo_key
        repo_dir.mkdir(parents=True, exist_ok=True)
        for result in repo_results:
            golden_path = repo_dir / f"{result['case_id']}.json"
    
def write_golden(golden_dir: Path, results: list[dict]) -> None:
    """Persist top retrieval results for manual regression review."""
    golden_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        golden_path = golden_dir / f"{result['case_id']}.json"
        golden_path.write_text(json.dumps(result, indent=2) + "\n")


def run_evaluation(
    repo_overrides: dict[str, Path],
    update_golden: bool = False,
    golden_dir: Path | None = None,
) -> dict:
    """Run all evaluation cases from per-repo golden directories."""
    golden_dir = golden_dir or DEFAULT_CASES_PATH
    results: list[dict] = []

    for repo_key, repo_root in sorted(repo_overrides.items()):
        try:
            cases = load_cases_for_repo(repo_key, golden_dir)
        except RuntimeError as e:
            print(f"Warning: {e}", file=sys.stderr)
            continue

        for case in cases:
            results.append(_run_case(case, repo_root))

    if update_golden:
        write_golden(golden_dir, results)

    return {
        "golden_dir": str(golden_dir),
        "repos": {key: str(path) for key, path in repo_overrides.items()},
        "summary": _aggregate(results),
        "results": results,
    }


def main():
    """CLI entry point: python3 -m code_graph_mcp.eval key=/path [key2=/path2] ..."""
    import sys

    if len(sys.argv) < 2:
        print(
            "Usage: python3 -m code_graph_mcp.eval telapp=/path/to/telapp [repo=/path2] ...",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        repo_overrides = parse_repo_overrides(sys.argv[1:])
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    result = run_evaluation(
        repo_overrides=repo_overrides,
        update_golden=False,
        golden_dir=DEFAULT_CASES_PATH,
    )

    # Pretty-print summary
    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(json.dumps(result["summary"], indent=2))
    print("=" * 80)
    print("\nPer-case results:")
    for res in result["results"]:
        print(
            f"\n  {res['case_id']:40s} "
            f"recall@5={res['recall_at_5']:.4f} "
            f"mrr={res['mrr']:.4f} "
            f"ndcg@10={res['ndcg_at_10']:.4f} "
            f"tokens={res['tokens_used']:6d}/{res['token_budget']:6d} "
            f"honesty={res['budget_honesty']:.4f}"
        )
    print("\n" + "=" * 80)
    
    # Write results to JSON for later comparison
    results_file = DEFAULT_CASES_PATH / "baseline.json"
    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nResults written to {results_file}")

    # Exit with error if any case failed hard
    if not all(r["returned_count"] > 0 for r in result["results"]):
        result["results"] and  print("\n⚠️  WARNING: Some cases returned no files!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()