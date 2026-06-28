"""Headless agent A/B harness: PCG context vs grep+read baseline (#16)."""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .eval import (
    EvalCase,
    agent_grep_top_files,
    estimate_paths_tokens,
    load_cases_for_repo,
    recall_at_k,
)
from .server import _handle_tool_call
from .store import Store

DEFAULT_AGENT_AB_BASELINE_PATH = Path("tests/eval/baseline-agent-ab.json")
BASELINE_READ_LIMIT = 3


@dataclass
class AgentArmResult:
    arm: str
    case_id: str
    repo_key: str
    tool_calls: int
    file_reads: int
    grep_calls: int
    tokens: int
    wall_time_ms: float
    recall_at_5: float
    paths: list[str]


def _median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def run_pcg_arm(case: EvalCase, repo_root: Path) -> AgentArmResult:
    store = Store(repo_root)
    try:
        if store.file_count() == 0:
            raise RuntimeError(f"Repo {repo_root} has no graph built yet. Run build first.")
    finally:
        store.close()

    started = time.perf_counter()
    raw = _handle_tool_call(
        repo_root,
        "pareto_context_graph",
        {
            "command": "explore" if not case.seed_files else "context",
            "files": case.seed_files,
            "query": case.query,
            "tier": case.tier,
            "token_budget": case.token_budget,
            "max_depth": case.max_depth,
            "min_weight": case.min_weight,
            "query_first": not case.seed_files,
            "session_memory": False,
            "feedback_log": False,
        },
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response = json.loads(raw)
    if "error" in response:
        raise RuntimeError(f"Case {case.case_id}: {response['error']}")

    paths = [entry["path"] for entry in response.get("context_files", [])]
    tokens = int(response.get("tokens_used", 0))
    return AgentArmResult(
        arm="pcg",
        case_id=case.case_id,
        repo_key=case.repo_key,
        tool_calls=1,
        file_reads=len(paths),
        grep_calls=0,
        tokens=tokens,
        wall_time_ms=round(elapsed_ms, 2),
        recall_at_5=round(recall_at_k(paths, case.expected_top_files, 5), 4),
        paths=paths[:10],
    )


def run_baseline_arm(
    case: EvalCase, repo_root: Path, *, read_limit: int = BASELINE_READ_LIMIT
) -> AgentArmResult:
    query = case.query or " ".join(case.seed_files)
    started = time.perf_counter()
    grep_calls = 0
    paths: list[str] = list(case.seed_files)

    if query.strip():
        grep_calls = 1
        paths.extend(agent_grep_top_files(repo_root, query, limit=read_limit))

    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            deduped.append(path)
    read_paths = deduped[: max(read_limit, len(case.seed_files)) or read_limit]
    tokens = estimate_paths_tokens(repo_root, read_paths)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    tool_calls = grep_calls + len(read_paths)

    return AgentArmResult(
        arm="baseline",
        case_id=case.case_id,
        repo_key=case.repo_key,
        tool_calls=tool_calls,
        file_reads=len(read_paths),
        grep_calls=grep_calls,
        tokens=tokens,
        wall_time_ms=round(elapsed_ms, 2),
        recall_at_5=round(recall_at_k(read_paths, case.expected_top_files, 5), 4),
        paths=read_paths,
    )


def run_agent_ab_study(
    repo_overrides: dict[str, Path],
    golden_dir: Path,
    *,
    read_limit: int = BASELINE_READ_LIMIT,
) -> dict:
    """Run paired PCG vs grep+read baseline on golden cases."""
    rows: list[dict] = []
    for repo_key, repo_root in sorted(repo_overrides.items()):
        try:
            cases = load_cases_for_repo(repo_key, golden_dir)
        except RuntimeError:
            continue
        for case in cases:
            pcg = run_pcg_arm(case, repo_root)
            baseline = run_baseline_arm(case, repo_root, read_limit=read_limit)
            rows.append(asdict(pcg))
            rows.append(asdict(baseline))

    summary = summarize_agent_ab(rows)
    return {
        "golden_dir": str(golden_dir),
        "repos": {key: str(path) for key, path in repo_overrides.items()},
        "cases": len(rows) // 2,
        "summary": summary,
        "results": rows,
    }


def summarize_agent_ab(rows: list[dict]) -> dict:
    pcg = [row for row in rows if row.get("arm") == "pcg"]
    baseline = [row for row in rows if row.get("arm") == "baseline"]

    def med(arm_rows: list[dict], key: str) -> float:
        return round(_median([float(row.get(key, 0)) for row in arm_rows]), 2)

    pcg_summary = {
        "tool_calls": med(pcg, "tool_calls"),
        "file_reads": med(pcg, "file_reads"),
        "grep_calls": med(pcg, "grep_calls"),
        "tokens": med(pcg, "tokens"),
        "wall_time_ms": med(pcg, "wall_time_ms"),
        "recall_at_5": med(pcg, "recall_at_5"),
    }
    baseline_summary = {
        "tool_calls": med(baseline, "tool_calls"),
        "file_reads": med(baseline, "file_reads"),
        "grep_calls": med(baseline, "grep_calls"),
        "tokens": med(baseline, "tokens"),
        "wall_time_ms": med(baseline, "wall_time_ms"),
        "recall_at_5": med(baseline, "recall_at_5"),
    }

    def pct_reduction(base: float, value: float) -> float | None:
        if base <= 0:
            return None
        return round(100.0 * (base - value) / base, 1)

    return {
        "pcg": pcg_summary,
        "baseline": baseline_summary,
        "pcg_vs_baseline": {
            "tool_calls_reduction_pct": pct_reduction(
                baseline_summary["tool_calls"], pcg_summary["tool_calls"]
            ),
            "file_reads_reduction_pct": pct_reduction(
                baseline_summary["file_reads"], pcg_summary["file_reads"]
            ),
            "tokens_reduction_pct": pct_reduction(
                baseline_summary["tokens"], pcg_summary["tokens"]
            ),
            "wall_time_reduction_pct": pct_reduction(
                baseline_summary["wall_time_ms"], pcg_summary["wall_time_ms"]
            ),
            "recall_at_5_delta": round(
                pcg_summary["recall_at_5"] - baseline_summary["recall_at_5"], 4
            ),
        },
    }


def portable_agent_ab_payload(payload: dict, base: Path | None = None) -> dict:
    from .eval import portable_eval_payload

    slim = {
        "golden_dir": payload.get("golden_dir"),
        "repos": payload.get("repos", {}),
        "cases": payload.get("cases"),
        "summary": payload.get("summary"),
    }
    return portable_eval_payload(slim, base=base)


def check_agent_ab_gate(current: dict, baseline: dict) -> dict:
    """Fail when PCG median recall drops >2pp vs baseline arm or vs stored PCG recall."""
    cur = current.get("summary", {})
    base = baseline.get("summary", {})
    failures: list[str] = []

    recall_delta = float(cur.get("pcg_vs_baseline", {}).get("recall_at_5_delta", 0.0))
    if recall_delta < -0.02:
        failures.append(f"PCG recall@5 delta vs baseline arm {recall_delta} (< -0.02)")

    stored_pcg_recall = float(base.get("pcg", {}).get("recall_at_5", 0.0))
    current_pcg_recall = float(cur.get("pcg", {}).get("recall_at_5", 0.0))
    if stored_pcg_recall > 0 and current_pcg_recall < stored_pcg_recall - 0.02:
        failures.append(
            f"PCG recall@5 regressed {current_pcg_recall} vs baseline file {stored_pcg_recall}"
        )

    tool_reduction = cur.get("pcg_vs_baseline", {}).get("tool_calls_reduction_pct")
    if tool_reduction is not None and float(tool_reduction) < 0:
        failures.append(f"PCG uses more tool calls than baseline ({tool_reduction}%)")

    return {"passed": not failures, "failures": failures, "summary": cur}
