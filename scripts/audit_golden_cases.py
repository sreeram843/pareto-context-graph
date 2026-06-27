#!/usr/bin/env python3
"""Fail if any golden case scores recall@5 = 0 on a built graph (Phase 9 gate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pareto_context_graph.eval import DEFAULT_CASES_PATH, load_cases_for_repo, run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-map",
        action="append",
        required=True,
        help="repo_key=path (repeatable)",
    )
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args()

    repo_overrides: dict[str, Path] = {}
    for item in args.repo_map:
        key, _, path = item.partition("=")
        repo_overrides[key] = Path(path).resolve()

    result = run_evaluation(
        repo_overrides=repo_overrides,
        golden_dir=args.golden_dir,
        isolate_cases=True,
    )
    zero = [r for r in result["results"] if float(r.get("recall_at_5", 0)) == 0.0]
    if zero:
        print("Cases with recall@5 = 0:", file=sys.stderr)
        for row in zero:
            print(f"  {row['case_id']}", file=sys.stderr)
        return 1

    summary = result["summary"]
    print(
        json.dumps(
            {
                "cases": summary.get("cases"),
                "mean_recall_at_5": summary.get("mean_recall_at_5"),
                "mean_mrr": summary.get("mean_mrr"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
