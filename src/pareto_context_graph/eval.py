"""Evaluation harness for retrieval quality regressions.

Runs the real ``pareto_context_graph`` context command against curated cases and reports
retrieval metrics so ranking changes can be validated before shipping.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from .ablation import ABLATION_SIGNALS
from .compress_stack import (
    aggregate_compress_stack,
    build_compress_stack_block,
    check_compress_stack_gate,
    compare_compress_baseline,
    compressed_tokens_from_row,
    legacy_compress_stack_fields,
    portable_compress_baseline_payload,
)
from .headroom_stack import aggregate_headroom_stack
from .server import _handle_tool_call
from .store import Store
from .tokens import estimate_file_tokens

DEFAULT_CASES_PATH = Path("tests/eval/golden")
DEFAULT_BASELINE_PATH = Path("tests/eval/baseline.json")
DEFAULT_COMPRESS_BASELINE_PATH = Path("tests/eval/baseline-compress.json")
PHASE9_FASTAPI_CONCEPT_BASELINE_PATH = Path("tests/eval/phase9-fastapi-concept.json")
REGRESSION_THRESHOLD = 0.02  # 2 percentage points on 0–1 metrics
PHASE11_CONCEPT_RECALL_LIFT = 0.05


def _portable_path(value: str, base: Path) -> str:
    try:
        return str(Path(value).resolve().relative_to(base.resolve()))
    except (ValueError, OSError):
        return value


def portable_eval_payload(payload: dict, base: Path | None = None) -> dict:
    """Rewrite absolute paths in eval output for committed baselines."""
    root = (base or Path.cwd()).resolve()
    out = dict(payload)
    if "golden_dir" in out:
        out["golden_dir"] = _portable_path(str(out["golden_dir"]), root)
    if "repos" in out:
        out["repos"] = {key: _portable_path(str(path), root) for key, path in out["repos"].items()}
    out["results"] = [
        {
            **row,
            "repo_root": _portable_path(str(row.get("repo_root", "")), root),
        }
        for row in out.get("results", [])
    ]
    return out


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
    category: str = ""
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
            seed_files=item.get("seed_files", []),
            query=item.get("query", ""),
            expected_top_files=item["expected_top_files"],
            tier=item.get("tier", 1),
            token_budget=item.get("token_budget", 50000),
            max_depth=item.get("max_depth", 2),
            min_weight=item.get("min_weight", 2),
            category=item.get("category", ""),
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


def recall_at_k(ranked: list[str], expected: list[str], k: int) -> float:
    if not expected:
        return 1.0
    hits = len(set(ranked[:k]) & set(expected))
    return hits / len(expected)


def mrr(ranked: list[str], expected: list[str]) -> float:
    expected_set = set(expected)
    for index, path in enumerate(ranked, start=1):
        if path in expected_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], expected: list[str], k: int) -> float:
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


def token_efficiency(tokens_used: int, expected_files_count: int) -> float:
    """Relevant hits per token used (higher is better)."""
    if expected_files_count == 0:
        return 1.0
    if tokens_used == 0:
        return 0.0
    return round(expected_files_count / tokens_used, 4)


def budget_honesty(tokens_used: int, token_budget: int) -> float:
    """1.0 when within budget; penalized when overshooting."""
    if tokens_used <= token_budget:
        return 1.0
    if token_budget == 0:
        return 0.0
    overshoot_ratio = (tokens_used - token_budget) / token_budget
    return round(max(0.0, 1.0 - overshoot_ratio), 4)


def payload_token_honesty(response: dict) -> float:
    """1.0 when tokens_used equals the sum of per-entry tokens_actual."""
    entries = response.get("context_files", [])
    reported = int(response.get("tokens_used", 0))
    if not entries:
        return 1.0 if reported == 0 else 0.0
    if "tokens_actual" not in entries[0]:
        return 1.0
    summed = sum(int(entry.get("tokens_actual", 0)) for entry in entries)
    return 1.0 if reported == summed else 0.0


def _query_terms(query: str) -> list[str]:
    terms = [t.lower() for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", query)]
    return terms[:12]


def agent_grep_top_files(repo_root: Path, query: str, limit: int = 3) -> list[str]:
    """Realistic agent baseline: grep tracked files, rank by match count."""
    terms = _query_terms(query)
    if not terms:
        return []

    pattern = "|".join(re.escape(t) for t in terms)
    result = subprocess.run(
        ["git", "grep", "-l", "-i", "-E", pattern],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        return []

    scores: dict[str, int] = {}
    for path in result.stdout.strip().splitlines():
        if not path:
            continue
        file_path = repo_root / path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(errors="ignore").lower()
        except OSError:
            continue
        scores[path] = sum(text.count(term) for term in terms)

    ranked = sorted(scores, key=lambda p: (-scores[p], p))
    return ranked[:limit]


def estimate_paths_tokens(repo_root: Path, paths: list[str]) -> int:
    total = 0
    for path in paths:
        fp = repo_root / path
        if fp.is_file():
            total += estimate_file_tokens(fp)
    return total


def corpus_token_estimate(repo_root: Path) -> int:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    paths = [p for p in result.stdout.strip().splitlines() if p]
    return estimate_paths_tokens(repo_root, paths)


def _run_case(
    case: EvalCase,
    repo_root: Path,
    *,
    compress_stack: bool = False,
    summary_prune: bool = False,
    learned_tier1_prune: bool = False,
) -> dict:
    store = Store(repo_root)
    try:
        if store.file_count() == 0:
            raise RuntimeError(f"Repo {repo_root} has no graph built yet. Run build first.")
    finally:
        store.close()

    raw = _handle_tool_call(
        repo_root,
        "pareto_context_graph",
        {
            "command": "context",
            "files": case.seed_files,
            "query": case.query,
            "tier": case.tier,
            "token_budget": case.token_budget,
            "max_depth": case.max_depth,
            "min_weight": case.min_weight,
            "query_first": not case.seed_files,
            "session_memory": False,
            **({"summary_prune": True} if summary_prune else {}),
            **({"learned_tier1_prune": True} if learned_tier1_prune else {}),
        },
    )
    response = json.loads(raw)
    if "error" in response:
        raise RuntimeError(f"Case {case.case_id}: {response['error']}")

    ranked = [entry["path"] for entry in response.get("context_files", [])]
    tokens_used = int(response.get("tokens_used", 0))
    expected_hits = len(set(ranked) & set(case.expected_top_files))

    agent_paths = agent_grep_top_files(repo_root, case.query or " ".join(case.seed_files))
    agent_tokens = estimate_paths_tokens(repo_root, agent_paths) if agent_paths else 0
    corpus_tokens = corpus_token_estimate(repo_root)

    result = {
        "case_id": case.case_id,
        "repo_key": case.repo_key,
        "category": case.category,
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
        "recall_at_5": round(recall_at_k(ranked, case.expected_top_files, 5), 4),
        "mrr": round(mrr(ranked, case.expected_top_files), 4),
        "ndcg_at_10": round(ndcg_at_k(ranked, case.expected_top_files, 10), 4),
        "token_efficiency": token_efficiency(tokens_used, expected_hits),
        "budget_honesty": budget_honesty(tokens_used, case.token_budget),
        "payload_honesty": payload_token_honesty(response),
        "agent_baseline_paths": agent_paths,
        "agent_baseline_tokens": agent_tokens,
        "agent_recall_at_5": round(recall_at_k(agent_paths, case.expected_top_files, 5), 4),
        "agent_mrr": round(mrr(agent_paths, case.expected_top_files), 4),
        "corpus_tokens": corpus_tokens,
        "reduction_vs_corpus": round(corpus_tokens / tokens_used, 2) if tokens_used else 0.0,
        "reduction_vs_agent": round(agent_tokens / tokens_used, 2)
        if tokens_used and agent_tokens
        else 0.0,
        "notes": case.notes,
    }

    if compress_stack:
        tier3_case = replace(
            case,
            tier=3,
            token_budget=max(case.token_budget, 20_000),
        )
        raw_t3 = _handle_tool_call(
            repo_root,
            "pareto_context_graph",
            {
                "command": "context",
                "files": tier3_case.seed_files,
                "query": tier3_case.query,
                "tier": tier3_case.tier,
                "token_budget": tier3_case.token_budget,
                "max_depth": tier3_case.max_depth,
                "min_weight": tier3_case.min_weight,
                "query_first": not tier3_case.seed_files,
                "compression": "prune",
            },
        )
        response_t3 = json.loads(raw_t3)
        if "error" not in response_t3 and response_t3.get("context_files"):
            stack = build_compress_stack_block(response_t3, query=case.query)
            if int(stack["graph_tokens"]) > 0:
                result.update(
                    {
                        "graph_tokens_tier3": stack["graph_tokens"],
                        "compressed_tokens": stack["compressed_tokens"],
                        "compressed_savings_ratio": stack["compressed_savings_ratio"],
                        "compression_method": stack["compression_method"],
                        "stack_reduction_vs_graph": stack["stack_reduction_vs_graph"],
                        "stack_reduction_vs_corpus": stack["stack_reduction_vs_corpus"],
                        "tier3_returned_count": len(response_t3.get("context_files", [])),
                        "tier3_content_hash": stack.get("content_hash"),
                        **legacy_compress_stack_fields(stack),
                    }
                )

    return result


def aggregate_by_category(results: list[dict]) -> dict[str, dict]:
    """Per-category metric rollups (e.g. community, concept, co_change)."""
    grouped: dict[str, list[dict]] = {}
    for row in results:
        category = str(row.get("category") or "uncategorized")
        grouped.setdefault(category, []).append(row)
    return {category: aggregate_results(rows) for category, rows in sorted(grouped.items())}


def aggregate_results(results: list[dict]) -> dict:
    count = len(results)
    if count == 0:
        return {
            "cases": 0,
            "mean_recall_at_5": 0.0,
            "mean_mrr": 0.0,
            "mean_ndcg_at_10": 0.0,
            "mean_tokens_used": 0.0,
            "mean_token_efficiency": 0.0,
            "mean_budget_honesty": 0.0,
            "mean_payload_honesty": 0.0,
            "mean_reduction_vs_corpus": 0.0,
            "mean_reduction_vs_agent": 0.0,
            "median_reduction_vs_agent": 0.0,
        }

    def mean(key: str) -> float:
        return round(sum(r[key] for r in results) / count, 4)

    def median(key: str) -> float:
        values = sorted(float(r[key]) for r in results)
        mid = len(values) // 2
        if len(values) % 2 == 1:
            return round(values[mid], 2)
        return round((values[mid - 1] + values[mid]) / 2, 2)

    return {
        "cases": count,
        "mean_recall_at_5": mean("recall_at_5"),
        "mean_mrr": mean("mrr"),
        "mean_ndcg_at_10": mean("ndcg_at_10"),
        "mean_tokens_used": round(sum(r["tokens_used"] for r in results) / count, 2),
        "mean_token_efficiency": mean("token_efficiency"),
        "mean_budget_honesty": mean("budget_honesty"),
        "mean_payload_honesty": mean("payload_honesty"),
        "mean_reduction_vs_corpus": mean("reduction_vs_corpus"),
        "mean_reduction_vs_agent": mean("reduction_vs_agent"),
        "median_reduction_vs_agent": median("reduction_vs_agent"),
        "three_way_benchmark": {
            "corpus_tokens_mean": round(sum(r.get("corpus_tokens", 0) for r in results) / count, 2),
            "agent_tokens_mean": round(
                sum(r.get("agent_baseline_tokens", 0) for r in results) / count, 2
            ),
            "graph_tokens_mean": round(sum(r.get("tokens_used", 0) for r in results) / count, 2),
            "median_reduction_vs_agent": median("reduction_vs_agent"),
            "mean_reduction_vs_corpus": mean("reduction_vs_corpus"),
        },
        **(
            {
                "compress_stack": aggregate_compress_stack(results),
                "headroom_stack": aggregate_headroom_stack(results),
            }
            if any(
                r.get("compressed_tokens") is not None or r.get("headroom_tokens") is not None
                for r in results
            )
            else {}
        ),
    }


def write_golden_snapshots(golden_dir: Path, results: list[dict]) -> None:
    """Persist per-case result snapshots under golden/<repo_key>/."""
    for result in results:
        repo_dir = golden_dir / result["repo_key"]
        repo_dir.mkdir(parents=True, exist_ok=True)
        golden_path = repo_dir / f"{result['case_id']}.json"
        golden_path.write_text(json.dumps(result, indent=2) + "\n")


GREP_COUNTERFACTUAL_RECALL_EPS = 1e-4


def agent_metrics_from_row(row: dict) -> tuple[float, float, int]:
    """Return (agent_recall_at_5, agent_mrr, agent_baseline_tokens) for one eval row."""
    paths = list(row.get("agent_baseline_paths") or [])
    expected = list(row.get("expected_top_files") or [])
    tokens = int(row.get("agent_baseline_tokens") or 0)
    if "agent_recall_at_5" in row:
        return float(row["agent_recall_at_5"]), float(row.get("agent_mrr", 0.0)), tokens
    return recall_at_k(paths, expected, 5), mrr(paths, expected), tokens


def check_grep_counterfactual_gate(results: list[dict]) -> dict:
    """Fail when graph Pareto-loses to grep-top-3 (worse recall and more tokens).

    Skips cases with no grep hits (``agent_baseline_tokens == 0``). Graph may use
    more tokens than grep when recall is strictly better, and may trade recall for
    token savings when ``reduction_vs_agent >= 1``.
    """
    failures: list[dict] = []
    skipped = 0
    compared = 0
    graph_not_losing = 0

    for row in results:
        agent_recall, _agent_mrr, agent_tokens = agent_metrics_from_row(row)
        if agent_tokens <= 0:
            skipped += 1
            continue
        compared += 1
        graph_recall = float(row.get("recall_at_5", 0.0))
        graph_tokens = int(row.get("tokens_used", 0))
        reduction = float(row.get("reduction_vs_agent", 0.0))
        grep_beats_recall = agent_recall > graph_recall + GREP_COUNTERFACTUAL_RECALL_EPS
        grep_beats_tokens = graph_tokens > agent_tokens

        if grep_beats_recall and grep_beats_tokens:
            failures.append(
                {
                    "case_id": row["case_id"],
                    "reason": "grep_pareto_dominates",
                    "graph_recall_at_5": graph_recall,
                    "agent_recall_at_5": round(agent_recall, 4),
                    "graph_tokens": graph_tokens,
                    "agent_baseline_tokens": agent_tokens,
                    "reduction_vs_agent": reduction,
                }
            )
        else:
            graph_not_losing += 1

    reductions = [
        float(row["reduction_vs_agent"])
        for row in results
        if int(row.get("agent_baseline_tokens") or 0) > 0
        and float(row.get("reduction_vs_agent", 0)) > 0
    ]
    median_reduction = 0.0
    if reductions:
        values = sorted(reductions)
        mid = len(values) // 2
        if len(values) % 2 == 1:
            median_reduction = values[mid]
        else:
            median_reduction = (values[mid - 1] + values[mid]) / 2

    return {
        "passed": len(failures) == 0,
        "compared_cases": compared,
        "skipped_no_agent_baseline": skipped,
        "graph_not_losing_cases": graph_not_losing,
        "failures": failures,
        "median_reduction_vs_agent_comparable": round(median_reduction, 2),
    }


def _fastapi_concept_rows(results: list[dict]) -> list[dict]:
    return [
        row
        for row in results
        if row.get("repo_key") == "fastapi" and row.get("category") == "concept"
    ]


def load_phase9_fastapi_concept_baseline(
    path: Path = PHASE9_FASTAPI_CONCEPT_BASELINE_PATH,
) -> float:
    if not path.is_file():
        return 0.5833
    payload = json.loads(path.read_text(encoding="utf-8"))
    return float(payload.get("mean_recall_at_5", 0.5833))


def check_phase11_fastapi_concept_gate(results: list[dict]) -> dict:
    """Phase 11: fastapi concept mean_recall@5 must beat Phase 9 by ≥5 pts."""
    rows = _fastapi_concept_rows(results)
    if not rows:
        return {"passed": True, "skipped": True, "reason": "no fastapi concept cases"}
    current = float(aggregate_results(rows)["mean_recall_at_5"])
    baseline = load_phase9_fastapi_concept_baseline()
    target = round(baseline + PHASE11_CONCEPT_RECALL_LIFT, 4)
    passed = current + 1e-9 >= target
    return {
        "passed": passed,
        "baseline_recall_at_5": baseline,
        "target_recall_at_5": target,
        "current_recall_at_5": current,
        "lift_required": PHASE11_CONCEPT_RECALL_LIFT,
        "cases": len(rows),
    }


def check_summary_prune_gate(
    repo_overrides: dict[str, Path],
    golden_dir: Path = DEFAULT_CASES_PATH,
) -> dict:
    """Phase 11.4: summary prune lowers tokens on query-only concept cases without recall loss."""
    repo_root = repo_overrides.get("fastapi")
    if repo_root is None:
        return {"passed": True, "skipped": True, "reason": "fastapi repo not in eval map"}

    cases = [
        case
        for case in load_cases_for_repo("fastapi", golden_dir)
        if case.category == "concept" and not case.seed_files and case.tier == 1
    ]
    if not cases:
        return {"passed": True, "skipped": True, "reason": "no query-only tier-1 concept cases"}

    baseline_rows = [_run_case(case, repo_root) for case in cases]
    pruned_rows = [_run_case(case, repo_root, summary_prune=True) for case in cases]
    base = aggregate_results(baseline_rows)
    pruned = aggregate_results(pruned_rows)
    token_delta = round(float(pruned["mean_tokens_used"]) - float(base["mean_tokens_used"]), 2)
    recall_delta = round(float(pruned["mean_recall_at_5"]) - float(base["mean_recall_at_5"]), 4)
    tokens_reduced = token_delta < -1.0
    recall_ok = recall_delta >= -REGRESSION_THRESHOLD
    failures: list[str] = []
    if not tokens_reduced:
        failures.append(
            f"mean_tokens_used did not drop (base={base['mean_tokens_used']}, pruned={pruned['mean_tokens_used']})"
        )
    if not recall_ok:
        failures.append(
            f"mean_recall_at_5 regressed by {recall_delta} (threshold={REGRESSION_THRESHOLD})"
        )
    return {
        "passed": tokens_reduced and recall_ok,
        "cases": len(cases),
        "baseline_mean_tokens": base["mean_tokens_used"],
        "pruned_mean_tokens": pruned["mean_tokens_used"],
        "token_delta": token_delta,
        "baseline_mean_recall_at_5": base["mean_recall_at_5"],
        "pruned_mean_recall_at_5": pruned["mean_recall_at_5"],
        "recall_delta": recall_delta,
        "failures": failures,
    }


def check_learned_tier1_prune_gate(
    repo_overrides: dict[str, Path],
    golden_dir: Path = DEFAULT_CASES_PATH,
) -> dict:
    """Phase 11.6: learned tier-1 prune lowers tokens without recall regression."""
    from .prune_learn import invalidate_prune_weights_cache, save_prune_weights

    repo_root = repo_overrides.get("fastapi")
    if repo_root is None:
        return {"passed": True, "skipped": True, "reason": "fastapi repo not in eval map"}

    cases = [
        case
        for case in load_cases_for_repo("fastapi", golden_dir)
        if case.category == "concept" and not case.seed_files and case.tier == 1
    ]
    if not cases:
        return {"passed": True, "skipped": True, "reason": "no query-only tier-1 concept cases"}

    weights_path = repo_root / ".pareto-context-graph" / "prune_weights.json"
    backup = weights_path.read_text(encoding="utf-8") if weights_path.is_file() else None
    try:
        baseline_rows = [_run_case(case, repo_root) for case in cases]
        weights: dict[str, float] = {}
        for case, row in zip(cases, baseline_rows, strict=True):
            expected = set(case.expected_top_files)
            for path in row.get("returned_paths", []):
                if path in expected:
                    weights[path] = max(weights.get(path, 0.0), 0.8)
                else:
                    weights[path] = min(weights.get(path, 0.0), -0.7)
        if not weights:
            return {"passed": True, "skipped": True, "reason": "no returned paths to weight"}
        save_prune_weights(repo_root, weights)
        invalidate_prune_weights_cache()

        pruned_rows = [_run_case(case, repo_root, learned_tier1_prune=True) for case in cases]
        base = aggregate_results(baseline_rows)
        pruned = aggregate_results(pruned_rows)
        token_delta = round(float(pruned["mean_tokens_used"]) - float(base["mean_tokens_used"]), 2)
        recall_delta = round(float(pruned["mean_recall_at_5"]) - float(base["mean_recall_at_5"]), 4)
        tokens_reduced = token_delta < -1.0
        recall_ok = recall_delta >= -REGRESSION_THRESHOLD
        failures: list[str] = []
        if not tokens_reduced:
            failures.append(
                f"mean_tokens_used did not drop (base={base['mean_tokens_used']}, pruned={pruned['mean_tokens_used']})"
            )
        if not recall_ok:
            failures.append(
                f"mean_recall_at_5 regressed by {recall_delta} (threshold={REGRESSION_THRESHOLD})"
            )
        return {
            "passed": tokens_reduced and recall_ok,
            "cases": len(cases),
            "baseline_mean_tokens": base["mean_tokens_used"],
            "pruned_mean_tokens": pruned["mean_tokens_used"],
            "token_delta": token_delta,
            "baseline_mean_recall_at_5": base["mean_recall_at_5"],
            "pruned_mean_recall_at_5": pruned["mean_recall_at_5"],
            "recall_delta": recall_delta,
            "failures": failures,
        }
    finally:
        if backup is None:
            if weights_path.is_file():
                weights_path.unlink()
        else:
            weights_path.write_text(backup, encoding="utf-8")
        invalidate_prune_weights_cache()


def compare_to_baseline(
    current: dict,
    baseline: dict,
    threshold: float = REGRESSION_THRESHOLD,
) -> dict:
    """Return regression report comparing summary metrics on shared cases."""
    metrics = ("mean_recall_at_5", "mean_mrr", "mean_ndcg_at_10")
    row_metrics = {
        "mean_recall_at_5": "recall_at_5",
        "mean_mrr": "mrr",
        "mean_ndcg_at_10": "ndcg_at_10",
    }
    baseline_ids = {row["case_id"] for row in baseline.get("results", [])}
    shared = [row for row in current.get("results", []) if row["case_id"] in baseline_ids]
    if not shared:
        shared = [row for row in current.get("results", []) if row.get("category") != "concept"]

    def mean_metric(summary_key: str) -> float:
        row_key = row_metrics[summary_key]
        if not shared:
            return 0.0
        return round(sum(float(row[row_key]) for row in shared) / len(shared), 4)

    cur = {metric: mean_metric(metric) for metric in metrics}
    baseline_shared = [
        row
        for row in baseline.get("results", [])
        if row["case_id"] in {r["case_id"] for r in shared}
    ]
    if baseline_shared:
        base = {
            metric: round(
                sum(float(row[row_metrics[metric]]) for row in baseline_shared)
                / len(baseline_shared),
                4,
            )
            for metric in metrics
        }
    else:
        base = {metric: float(baseline.get("summary", {}).get(metric, 0.0)) for metric in metrics}

    regressions: list[dict] = []
    for metric in metrics:
        cur_val = float(cur.get(metric, 0.0))
        base_val = float(base.get(metric, 0.0))
        delta = round(cur_val - base_val, 4)
        if delta < -threshold:
            regressions.append(
                {
                    "metric": metric,
                    "baseline": base_val,
                    "current": cur_val,
                    "delta": delta,
                }
            )
    return {
        "passed": len(regressions) == 0,
        "threshold": threshold,
        "regressions": regressions,
        "baseline_cases": len(baseline_shared) if baseline_shared else base.get("cases", 0),
        "current_cases": len(shared),
        "shared_case_ids": sorted({row["case_id"] for row in shared}),
    }


def run_evaluation(
    repo_overrides: dict[str, Path],
    update_golden: bool = False,
    golden_dir: Path | None = None,
    *,
    compress_stack: bool = False,
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
            results.append(_run_case(case, repo_root, compress_stack=compress_stack))

    if update_golden:
        write_golden_snapshots(golden_dir, results)

    summary = aggregate_results(results)
    summary["by_category"] = aggregate_by_category(results)

    return {
        "golden_dir": str(golden_dir),
        "repos": {key: str(path) for key, path in repo_overrides.items()},
        "summary": summary,
        "results": results,
    }


@contextmanager
def ablation_env(signal: str):
    """Temporarily ablate one retrieval signal via PCG_ABLATE_<SIGNAL>=1."""
    key = f"PCG_ABLATE_{signal.upper()}"
    prev = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def run_ablation_study(
    repo_overrides: dict[str, Path],
    golden_dir: Path | None = None,
    *,
    compress_stack: bool = False,
) -> dict:
    """Run golden eval with each signal ablated; report recall@5 deltas."""
    baseline = run_evaluation(repo_overrides, golden_dir=golden_dir, compress_stack=compress_stack)
    baseline_recall = float(baseline.get("summary", {}).get("mean_recall_at_5", 0.0))
    rows: list[dict] = []
    for signal in ABLATION_SIGNALS:
        with ablation_env(signal):
            result = run_evaluation(
                repo_overrides, golden_dir=golden_dir, compress_stack=compress_stack
            )
        recall = float(result.get("summary", {}).get("mean_recall_at_5", 0.0))
        rows.append(
            {
                "signal": signal,
                "recall_at_5": recall,
                "delta": round(recall - baseline_recall, 4),
            }
        )
    return {
        "baseline_recall_at_5": baseline_recall,
        "ablations": rows,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: python3 -m pareto_context_graph.eval [options] key=/path ..."""
    import argparse

    parser = argparse.ArgumentParser(description="Run retrieval eval cases")
    parser.add_argument("repo_map", nargs="*", help="Repo mappings: fastapi=/path/to/fastapi")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument(
        "--update-baseline", action="store_true", help="Write results to baseline.json"
    )
    parser.add_argument("--check-baseline", action="store_true", help="Exit 1 on metric regression")
    parser.add_argument("--update-golden", action="store_true", help="Write per-case snapshots")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--threshold", type=float, default=REGRESSION_THRESHOLD)
    parser.add_argument(
        "--compress-stack",
        "--headroom-stack",
        dest="compress_stack",
        action="store_true",
        help="Also run tier-3 context and report graph → prune compression token savings",
    )
    parser.add_argument(
        "--compress-baseline",
        type=Path,
        default=DEFAULT_COMPRESS_BASELINE_PATH,
        help="Compression regression baseline (default: tests/eval/baseline-compress.json)",
    )
    parser.add_argument(
        "--update-compress-baseline",
        action="store_true",
        help="Write compress_stack summary to --compress-baseline",
    )
    parser.add_argument(
        "--check-compress-baseline",
        action="store_true",
        help="With --compress-stack: fail if tier-3 compression regresses vs compress baseline",
    )
    parser.add_argument(
        "--ablation",
        action="store_true",
        help="Run per-signal ablation study (PCG_ABLATE_*); prints recall@5 deltas",
    )
    args = parser.parse_args(argv)

    if args.check_compress_baseline and not args.compress_stack:
        parser.error("--check-compress-baseline requires --compress-stack")
    if args.update_compress_baseline and not args.compress_stack:
        parser.error("--update-compress-baseline requires --compress-stack")

    if not args.repo_map:
        parser.error("Provide at least one repo mapping: fastapi=/path/to/fastapi")

    try:
        repo_overrides = parse_repo_overrides(args.repo_map)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.ablation:
        ablation_result = run_ablation_study(
            repo_overrides=repo_overrides,
            golden_dir=args.golden_dir,
            compress_stack=args.compress_stack,
        )
        if args.json:
            print(json.dumps(ablation_result, indent=2))
        else:
            print("\n" + "=" * 72)
            print("ABLATION STUDY (recall@5 vs baseline)")
            print("=" * 72)
            print(f"baseline recall@5: {ablation_result['baseline_recall_at_5']:.4f}")
            for row in ablation_result["ablations"]:
                print(
                    f"  PCG_ABLATE_{row['signal'].upper():8s} "
                    f"recall@5={row['recall_at_5']:.4f} delta={row['delta']:+.4f}"
                )
            print("=" * 72)
        return 0

    result = run_evaluation(
        repo_overrides=repo_overrides,
        update_golden=args.update_golden,
        golden_dir=args.golden_dir,
        compress_stack=args.compress_stack,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("\n" + "=" * 72)
        print("EVALUATION SUMMARY")
        print("=" * 72)
        print(json.dumps(result["summary"], indent=2))
        print("=" * 72)
        for res in result["results"]:
            line = (
                f"  {res['case_id']:32s} "
                f"recall@5={res['recall_at_5']:.3f} "
                f"mrr={res['mrr']:.3f} "
                f"ndcg@10={res['ndcg_at_10']:.3f} "
                f"tokens={res['tokens_used']:5d} "
                f"vs_agent={res['reduction_vs_agent']:.1f}x"
            )
            if compressed_tokens_from_row(res) is not None:
                compressed = compressed_tokens_from_row(res)
                line += (
                    f"  tier3={res.get('graph_tokens_tier3', 0):5d}"
                    f"→cmp={compressed:5d}"
                    f" ({res.get('stack_reduction_vs_graph', 0):.1f}x)"
                )
            print(line)
        print("=" * 72)

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        portable = portable_eval_payload(result)
        args.baseline.write_text(json.dumps(portable, indent=2) + "\n")
        print(f"Baseline written to {args.baseline}", file=sys.stderr)

    if args.update_compress_baseline:
        args.compress_baseline.parent.mkdir(parents=True, exist_ok=True)
        portable = portable_compress_baseline_payload(result)
        args.compress_baseline.write_text(json.dumps(portable, indent=2) + "\n")
        print(f"Compress baseline written to {args.compress_baseline}", file=sys.stderr)

    exit_code = 0
    if args.compress_stack:
        gate = check_compress_stack_gate(result)
        if not args.json:
            print(json.dumps({"compress_gate": gate}, indent=2))
        if not gate["passed"]:
            print("REGRESSION: compress_stack sanity gate failed", file=sys.stderr)
            for item in gate.get("failures", []):
                print(f"  {item}", file=sys.stderr)
            exit_code = 1

    if args.check_baseline:
        if not args.baseline.exists():
            print(f"error: baseline missing at {args.baseline}", file=sys.stderr)
            return 1
        baseline = json.loads(args.baseline.read_text())
        report = compare_to_baseline(result, baseline, threshold=args.threshold)
        if not args.json:
            print(json.dumps(report, indent=2))
        if not report["passed"]:
            print("REGRESSION: metrics dropped beyond threshold", file=sys.stderr)
            exit_code = 1
        mean_budget = float(result["summary"].get("mean_budget_honesty", 0.0))
        if mean_budget < 0.95:
            print(
                f"REGRESSION: mean_budget_honesty {mean_budget:.4f} < 0.95",
                file=sys.stderr,
            )
            exit_code = 1
        mean_payload = float(result["summary"].get("mean_payload_honesty", 0.0))
        if mean_payload < 1.0:
            print(
                f"REGRESSION: mean_payload_honesty {mean_payload:.4f} < 1.0",
                file=sys.stderr,
            )
            exit_code = 1
        concept_results = [r for r in result["results"] if r.get("category") == "concept"]
        for res in concept_results:
            if not res.get("seed_files"):
                top10 = set(res.get("returned_paths", [])[:10])
                hits = len(top10 & set(res.get("expected_top_files", [])))
                if hits < 3:
                    print(
                        f"REGRESSION: concept case {res['case_id']} "
                        f"only {hits}/3 expected files in top 10",
                        file=sys.stderr,
                    )
                    exit_code = 1

        grep_gate = check_grep_counterfactual_gate(result["results"])
        if not args.json:
            print(json.dumps({"grep_counterfactual_gate": grep_gate}, indent=2))
        if not grep_gate["passed"]:
            print("REGRESSION: graph loses to grep baseline on one or more cases", file=sys.stderr)
            for item in grep_gate.get("failures", []):
                print(f"  {item['case_id']}: {item}", file=sys.stderr)
            exit_code = 1

        phase11_gate = check_phase11_fastapi_concept_gate(result["results"])
        if not args.json:
            print(json.dumps({"phase11_fastapi_concept_gate": phase11_gate}, indent=2))
        if not phase11_gate.get("skipped") and not phase11_gate["passed"]:
            print(
                "REGRESSION: fastapi concept recall below Phase 9 + 5pts gate",
                file=sys.stderr,
            )
            exit_code = 1

        prune_gate = check_summary_prune_gate(repo_overrides, golden_dir=args.golden_dir)
        if not args.json:
            print(json.dumps({"summary_prune_gate": prune_gate}, indent=2))
        if not prune_gate.get("skipped") and not prune_gate["passed"]:
            print("REGRESSION: summary_prune gate failed on fastapi concept cases", file=sys.stderr)
            for item in prune_gate.get("failures", []):
                print(f"  {item}", file=sys.stderr)
            exit_code = 1

        learned_tier1_gate = check_learned_tier1_prune_gate(
            repo_overrides, golden_dir=args.golden_dir
        )
        if not args.json:
            print(json.dumps({"learned_tier1_prune_gate": learned_tier1_gate}, indent=2))
        if not learned_tier1_gate.get("skipped") and not learned_tier1_gate["passed"]:
            print(
                "REGRESSION: learned_tier1_prune gate failed on fastapi concept cases",
                file=sys.stderr,
            )
            for item in learned_tier1_gate.get("failures", []):
                print(f"  {item}", file=sys.stderr)
            exit_code = 1

    if args.check_compress_baseline:
        if not args.compress_baseline.exists():
            print(f"error: compress baseline missing at {args.compress_baseline}", file=sys.stderr)
            return 1
        compress_baseline = json.loads(args.compress_baseline.read_text())
        compress_report = compare_compress_baseline(result, compress_baseline)
        if not args.json:
            print(json.dumps({"compress_regression": compress_report}, indent=2))
        if not compress_report["passed"]:
            print("REGRESSION: compress metrics regressed vs baseline", file=sys.stderr)
            for item in compress_report.get("failures", []):
                print(f"  {item}", file=sys.stderr)
            exit_code = 1

    if result["summary"]["cases"] == 0:
        print("error: no cases ran", file=sys.stderr)
        return 1

    if any(r["returned_count"] == 0 for r in result["results"]):
        print("warning: some cases returned no files", file=sys.stderr)
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
