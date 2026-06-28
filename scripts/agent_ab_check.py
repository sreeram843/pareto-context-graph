#!/usr/bin/env python3
"""Phase 1.5 — agent A/B gate. Fail CI if pcg loses to the baseline arm.

Reads the agent-ab.json produced by scripts/agent_bench.sh and asserts that, for every
flow, the pcg arm reduced total tokens and tool calls vs the empty-MCP baseline arm
(and had no errored runs). Exits non-zero on regression.

Usage: python3 scripts/agent_ab_check.py [path-to-agent-ab.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pareto_context_graph.agent_transcript import check_agent_ab_gate

DEFAULT = Path("tests/eval/agent-ab.json")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.is_file():
        print(f"agent A/B gate: {path} not found (run scripts/agent_bench.sh first)")
        return 0  # nothing to gate yet — don't fail a repo that hasn't run the bench
    payload = json.loads(path.read_text())
    result = check_agent_ab_gate(payload)
    for flow in payload.get("flows", []):
        red = flow.get("reductions", {})
        print(
            f"- {flow.get('flow_id')}: tokens -{red.get('total_tokens_reduction_pct')}% "
            f"reads -{red.get('read_calls_reduction_pct')}% "
            f"cost -{red.get('total_cost_usd_reduction_pct')}%"
        )
    if result["passed"]:
        print("Agent A/B gate: PASS")
        return 0
    print("Agent A/B gate: FAIL")
    for f in result["failures"]:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
