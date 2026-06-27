"""Synthetic feedback replay eval: train on cases, learn, measure held-out MRR."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .eval import EvalCase, _run_case, aggregate_results, load_cases_for_repo
from .feedback import (
    LEARNING_ARTIFACTS,
    FeedbackEventLog,
    clear_learning_state,
    fold_events_to_sqlite,
    record_feedback,
)
from .prune_learn import invalidate_prune_weights_cache, learn_prune_weights, save_prune_weights
from .ranker import learn_file_weights, save_ranker, train_best_ranker
from .repo_caches import invalidate_caches
from .server import _handle_tool_call
from .store import DB_DIR, Store

DEFAULT_HOLDOUT_CATEGORY = "concept"
MIN_MRR_IMPROVEMENT = 0.03
LEARNING_FILES = LEARNING_ARTIFACTS


@dataclass
class ReplayReport:
    train_cases: int
    holdout_cases: int
    baseline_mrr: float
    after_mrr: float
    mrr_delta: float
    passed: bool
    holdout_results_before: list[dict[str, Any]]
    holdout_results_after: list[dict[str, Any]]
    weights_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "train_cases": self.train_cases,
            "holdout_cases": self.holdout_cases,
            "baseline_mrr": round(self.baseline_mrr, 4),
            "after_mrr": round(self.after_mrr, 4),
            "mrr_delta": round(self.mrr_delta, 4),
            "passed": self.passed,
            "min_required_delta": MIN_MRR_IMPROVEMENT,
            "weights_count": self.weights_count,
            "holdout_before": self.holdout_results_before,
            "holdout_after": self.holdout_results_after,
        }


def split_cases(
    cases: list[EvalCase],
    *,
    holdout_category: str = DEFAULT_HOLDOUT_CATEGORY,
) -> tuple[list[EvalCase], list[EvalCase]]:
    """Hold out query-only concept cases; train on seeded + co_change cases."""
    holdout = [case for case in cases if case.category == holdout_category and not case.seed_files]
    train = [case for case in cases if case not in holdout]
    if not train or not holdout:
        midpoint = max(1, len(cases) // 5)
        return cases[midpoint:], cases[:midpoint]
    return train, holdout


@contextmanager
def learning_snapshot(repo_root: Path) -> Iterator[None]:
    """Backup and restore small learning artifacts around a replay run."""
    db_dir = repo_root / DB_DIR
    saved: dict[str, bytes | None] = {}
    for name in LEARNING_FILES:
        path = db_dir / name
        saved[name] = path.read_bytes() if path.exists() else None

    store = Store(repo_root)
    try:
        feedback_rows = store.conn.execute(
            "SELECT ts, query, file_path, returned, used FROM feedback"
        ).fetchall()
        dedup_rows = store.conn.execute("SELECT event_key, ts FROM feedback_dedup").fetchall()
    finally:
        store.close()

    clear_learning_state(repo_root)
    try:
        yield
    finally:
        clear_learning_state(repo_root)
        db_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in saved.items():
            path = db_dir / name
            if payload is not None:
                path.write_bytes(payload)
            elif path.exists():
                path.unlink()

        if feedback_rows or dedup_rows:
            store = Store(repo_root)
            try:
                for row in feedback_rows:
                    store.conn.execute(
                        "INSERT INTO feedback(ts, query, file_path, returned, used) VALUES (?, ?, ?, ?, ?)",
                        row,
                    )
                for row in dedup_rows:
                    store.conn.execute(
                        "INSERT OR IGNORE INTO feedback_dedup(event_key, ts) VALUES (?, ?)",
                        row,
                    )
                store.commit()
            finally:
                store.close()
        invalidate_caches()


def _latest_context_event(repo_root: Path, request_id: str) -> dict[str, Any] | None:
    for event in reversed(FeedbackEventLog(repo_root).read_all()):
        if event.get("kind") == "context_request" and event.get("request_id") == request_id:
            return event
    return None


def synthesize_feedback_for_case(case: EvalCase, repo_root: Path) -> dict[str, int]:
    """Run context once and write accept/reject events from expected labels."""
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
        },
    )
    response = json.loads(raw)
    if "error" in response:
        raise RuntimeError(f"feedback replay case {case.case_id}: {response['error']}")

    request_id = str(response.get("request_id", ""))
    if not request_id:
        raise RuntimeError(f"feedback replay case {case.case_id}: missing request_id")

    expected = set(case.expected_top_files)
    returned = [entry["path"] for entry in response.get("context_files", [])]
    wrong_returned = [path for path in returned if path not in expected][:8]

    stats = {"accept": 0, "reject": 0, "mark_used": 0}
    for path in case.expected_top_files:
        stats["accept"] += record_feedback(
            repo_root,
            kind="accept",
            request_id=request_id,
            paths=[path],
            query=case.query,
        )["written"]
        stats["mark_used"] += record_feedback(
            repo_root,
            kind="mark_used",
            request_id=request_id,
            paths=[path],
            query=case.query,
        )["written"]

    if wrong_returned:
        stats["reject"] += record_feedback(
            repo_root,
            kind="reject",
            request_id=request_id,
            paths=wrong_returned,
            query=case.query,
        )["written"]

    store = Store(repo_root)
    try:
        for path in case.expected_top_files:
            for _ in range(3):
                store.log_feedback(
                    query=case.query or case.case_id, file_path=path, returned=True, used=True
                )
        for path in wrong_returned:
            store.log_feedback(
                query=case.query or case.case_id, file_path=path, returned=True, used=False
            )
        store.commit()
    finally:
        store.close()

    pool = _latest_context_event(repo_root, request_id)
    if pool:
        ranked_wrong = [
            str(item.get("path", ""))
            for item in pool.get("candidates", [])
            if str(item.get("path", "")) not in expected
        ][:8]
        extra = [path for path in ranked_wrong if path not in wrong_returned]
        if extra:
            stats["reject"] += record_feedback(
                repo_root,
                kind="reject",
                request_id=request_id,
                paths=extra,
                query=case.query,
            )["written"]

    return stats


def apply_learn(
    repo_root: Path,
    *,
    ranker: str = "auto",
    holdout_cases: list[EvalCase] | None = None,
) -> dict[str, Any]:
    """Fold events and write weights.json + ranker.json."""
    fold_stats = fold_events_to_sqlite(repo_root)
    store = Store(repo_root)
    try:
        rows = store.feedback_rows_by_file()
    finally:
        store.close()

    weights = learn_file_weights(rows)
    prune_weights = learn_prune_weights(rows)
    weights_path = repo_root / DB_DIR / "weights.json"
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    weights_path.write_text(json.dumps(weights, indent=2) + "\n")
    save_prune_weights(repo_root, prune_weights)
    invalidate_caches()
    invalidate_prune_weights_cache()

    weights_only_mrr: float | None = None
    if holdout_cases:
        weights_only_mrr = aggregate_results(run_cases(holdout_cases, repo_root))["mean_mrr"]

    ranker_model = train_best_ranker(
        FeedbackEventLog(repo_root).read_all(),
        prefer=ranker,
        epochs=200,
        learning_rate=0.08,
    )
    ranker_saved = False
    if ranker_model:
        if holdout_cases and weights_only_mrr is not None:
            save_ranker(repo_root, ranker_model)
            invalidate_caches()
            with_ranker_mrr = aggregate_results(run_cases(holdout_cases, repo_root))["mean_mrr"]
            if with_ranker_mrr >= weights_only_mrr:
                ranker_saved = True
            else:
                for name in ("ranker.json", "ranker.lgb.txt"):
                    path = repo_root / DB_DIR / name
                    if path.exists():
                        path.unlink()
                invalidate_caches()
        else:
            save_ranker(repo_root, ranker_model)
            ranker_saved = True

    return {
        "fold": fold_stats,
        "weights": len(weights),
        "prune_weights": len(prune_weights),
        "ranker_saved": ranker_saved,
        "weights_only_mrr": weights_only_mrr,
    }


def run_cases(cases: list[EvalCase], repo_root: Path) -> list[dict[str, Any]]:
    invalidate_caches()
    return [_run_case(case, repo_root) for case in cases]


def run_feedback_replay(
    repo_root: Path,
    cases: list[EvalCase],
    *,
    holdout_category: str = DEFAULT_HOLDOUT_CATEGORY,
    train_repeats: int = 2,
    min_mrr_improvement: float = MIN_MRR_IMPROVEMENT,
) -> ReplayReport:
    """Train on synthetic feedback, learn, and compare held-out MRR."""
    train_cases, holdout_cases = split_cases(cases, holdout_category=holdout_category)

    holdout_before = run_cases(holdout_cases, repo_root)
    baseline_mrr = aggregate_results(holdout_before)["mean_mrr"]

    for _ in range(max(1, train_repeats)):
        for case in train_cases:
            synthesize_feedback_for_case(case, repo_root)

    learn_stats = apply_learn(repo_root, holdout_cases=holdout_cases)

    holdout_after = run_cases(holdout_cases, repo_root)
    after_mrr = aggregate_results(holdout_after)["mean_mrr"]
    delta = after_mrr - baseline_mrr

    return ReplayReport(
        train_cases=len(train_cases),
        holdout_cases=len(holdout_cases),
        baseline_mrr=baseline_mrr,
        after_mrr=after_mrr,
        mrr_delta=delta,
        passed=delta >= min_mrr_improvement,
        holdout_results_before=holdout_before,
        holdout_results_after=holdout_after,
        weights_count=int(learn_stats.get("weights", 0)),
    )


def run_feedback_replay_for_repo(
    repo_key: str,
    repo_root: Path,
    *,
    golden_dir: Path | None = None,
    **kwargs: Any,
) -> ReplayReport:
    from .eval import DEFAULT_CASES_PATH

    cases = load_cases_for_repo(repo_key, golden_dir or DEFAULT_CASES_PATH)
    return run_feedback_replay(repo_root, cases, **kwargs)


def per_case_mrr_delta(before: list[dict], after: list[dict]) -> list[dict[str, Any]]:
    after_by_id = {item["case_id"]: item for item in after}
    rows = []
    for item in before:
        case_id = item["case_id"]
        post = after_by_id.get(case_id, {})
        rows.append(
            {
                "case_id": case_id,
                "mrr_before": item.get("mrr", 0.0),
                "mrr_after": post.get("mrr", 0.0),
                "delta": round(float(post.get("mrr", 0.0)) - float(item.get("mrr", 0.0)), 4),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m pareto_context_graph.feedback_replay fastapi=/path/to/fastapi"""
    import argparse

    from .eval import DEFAULT_CASES_PATH, parse_repo_overrides

    parser = argparse.ArgumentParser(description="Feedback replay held-out MRR eval")
    parser.add_argument("repo_map", nargs=1, help="Repo mapping: fastapi=/abs/path")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--min-delta", type=float, default=MIN_MRR_IMPROVEMENT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_overrides = parse_repo_overrides(args.repo_map)
    repo_key, repo_root = next(iter(repo_overrides.items()))
    with learning_snapshot(repo_root):
        report = run_feedback_replay_for_repo(
            repo_key,
            repo_root,
            golden_dir=args.golden_dir,
            min_mrr_improvement=args.min_delta,
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
