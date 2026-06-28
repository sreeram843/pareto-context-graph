#!/usr/bin/env python3
"""Aggregate one flow's agent-A/B runs into median metrics + reductions.

Thin I/O wrapper around pareto_context_graph.agent_transcript (unit-tested). Reads two
"list files" (one transcript path per line) for the pcg and baseline arms, parses each
stream-json transcript, and prints the per-flow comparison JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pareto_context_graph.agent_transcript import arm_comparison, parse_stream_json


def _load_runs(list_file: str):
    runs = []
    for line in Path(list_file).read_text().splitlines():
        line = line.strip()
        if line and Path(line).is_file():
            runs.append(parse_stream_json(Path(line).read_text()))
    return runs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow-id", required=True)
    ap.add_argument("--pcg-runs", required=True, help="file listing pcg transcript paths")
    ap.add_argument("--baseline-runs", required=True, help="file listing baseline transcript paths")
    args = ap.parse_args()

    comparison = arm_comparison(_load_runs(args.pcg_runs), _load_runs(args.baseline_runs))
    print(json.dumps({"flow_id": args.flow_id, **comparison}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
