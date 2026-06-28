"""Parse `claude -p --output-format stream-json` transcripts into agent metrics.

This is the measurement core of the real agent A/B (Phase 1.3): run a coding agent
on a flow question twice — once WITH the pcg MCP server, once with an empty MCP
config — and compare how much each arm read and spent. The shell driver
(`scripts/agent_bench.sh`) captures each run's stream-json and pipes it here.

The parser is deliberately tolerant of the exact event shape: Claude Code emits a
JSONL stream of ``assistant`` / ``user`` / ``result`` events, and we only depend on
the stable bits (``type``, ``tool_use`` blocks, ``usage`` counts, and the terminal
``result`` summary).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field

__all__ = [
    "TranscriptMetrics",
    "aggregate_runs",
    "arm_comparison",
    "check_agent_ab_gate",
    "classify_tool",
    "parse_stream_json",
]

# Numeric fields aggregated (median) across the N runs of one arm.
_AGG_FIELDS = (
    "total_tool_calls",
    "read_calls",
    "search_calls",
    "pcg_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "num_turns",
    "duration_ms",
    "total_cost_usd",
)
# Lower-is-better fields where we report pcg's reduction vs the baseline arm.
_REDUCTION_FIELDS = (
    "total_tool_calls",
    "read_calls",
    "search_calls",
    "total_tokens",
    "num_turns",
    "total_cost_usd",
)

# Map raw tool names to the coarse buckets we report on. Anything matching the pcg
# MCP server (``mcp__pareto_context_graph__*`` or ``mcp__pcg__*``) is "pcg"; generic
# file-reading / searching tools are the grep-style work the tool is meant to remove.
_READ_TOOLS = frozenset({"Read", "NotebookRead", "View", "cat"})
_SEARCH_TOOLS = frozenset({"Grep", "Glob", "Search", "find", "rg"})
_EXEC_TOOLS = frozenset({"Bash", "Shell", "Execute"})
_EXPLORE_TOOLS = frozenset({"Task", "Agent", "Explore"})


def classify_tool(name: str) -> str:
    """Bucket a tool name into pcg / read / search / exec / explore / other."""
    lowered = name.lower()
    if lowered.startswith("mcp__") and (
        "pareto_context_graph" in lowered or "pcg" in lowered
    ):
        return "pcg"
    if name in _READ_TOOLS:
        return "read"
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _EXEC_TOOLS:
        return "exec"
    if name in _EXPLORE_TOOLS:
        return "explore"
    return "other"


@dataclass
class TranscriptMetrics:
    """Aggregate metrics for one agent run (one arm of the A/B)."""

    tool_calls: Counter = field(default_factory=Counter)
    tool_buckets: Counter = field(default_factory=Counter)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    num_turns: int = 0
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    result_text: str = ""
    is_error: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_tool_calls(self) -> int:
        return sum(self.tool_calls.values())

    def to_dict(self) -> dict:
        return {
            "tool_calls": dict(self.tool_calls),
            "tool_buckets": dict(self.tool_buckets),
            "total_tool_calls": self.total_tool_calls,
            "read_calls": self.tool_buckets.get("read", 0),
            "search_calls": self.tool_buckets.get("search", 0),
            "pcg_calls": self.tool_buckets.get("pcg", 0),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
            "num_turns": self.num_turns,
            "duration_ms": self.duration_ms,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "is_error": self.is_error,
            "result_text": self.result_text,
        }


def _add_usage(metrics: TranscriptMetrics, usage: dict) -> None:
    metrics.input_tokens += int(usage.get("input_tokens", 0) or 0)
    metrics.output_tokens += int(usage.get("output_tokens", 0) or 0)
    metrics.cache_read_tokens += int(usage.get("cache_read_input_tokens", 0) or 0)


def parse_stream_json(lines: list[str] | str) -> TranscriptMetrics:
    """Parse stream-json (JSONL) into :class:`TranscriptMetrics`.

    Token totals and turn/cost/duration are taken from the terminal ``result`` event
    when present (the authoritative summary); per-turn ``usage`` is summed only as a
    fallback when no result summary is emitted. ``tool_use`` blocks are always counted
    from the assistant turns.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()

    metrics = TranscriptMetrics()
    summed_input = 0
    summed_output = 0
    summed_cache = 0
    saw_result = False

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        etype = event.get("type")

        if etype == "assistant":
            message = event.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "unknown"))
                    metrics.tool_calls[name] += 1
                    metrics.tool_buckets[classify_tool(name)] += 1
            usage = message.get("usage")
            if isinstance(usage, dict):
                summed_input += int(usage.get("input_tokens", 0) or 0)
                summed_output += int(usage.get("output_tokens", 0) or 0)
                summed_cache += int(usage.get("cache_read_input_tokens", 0) or 0)

        elif etype == "result":
            saw_result = True
            metrics.num_turns = int(event.get("num_turns", 0) or 0)
            metrics.duration_ms = int(event.get("duration_ms", 0) or 0)
            metrics.total_cost_usd = float(event.get("total_cost_usd", 0.0) or 0.0)
            metrics.is_error = bool(event.get("is_error", False)) or (
                event.get("subtype") not in (None, "success")
            )
            result_val = event.get("result")
            if isinstance(result_val, str):
                metrics.result_text = result_val
            usage = event.get("usage")
            if isinstance(usage, dict):
                _add_usage(metrics, usage)

    if not saw_result or metrics.total_tokens == 0:
        # Fall back to per-turn usage when the result summary lacked token counts.
        metrics.input_tokens = metrics.input_tokens or summed_input
        metrics.output_tokens = metrics.output_tokens or summed_output
        metrics.cache_read_tokens = metrics.cache_read_tokens or summed_cache

    return metrics


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    if len(s) % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def aggregate_runs(runs: list[TranscriptMetrics]) -> dict:
    """Median of each numeric field across the runs of one arm."""
    dicts = [m.to_dict() for m in runs]
    out: dict = {"runs": len(runs)}
    for field_name in _AGG_FIELDS:
        out[field_name] = round(_median([float(d.get(field_name, 0)) for d in dicts]), 4)
    out["errors"] = sum(1 for m in runs if m.is_error)
    return out


def _pct_reduction(baseline: float, pcg: float) -> float | None:
    """Percent reduction of pcg vs baseline (positive = pcg used less). None if N/A."""
    if baseline <= 0:
        return None
    return round((baseline - pcg) / baseline * 100.0, 1)


def arm_comparison(pcg_runs: list[TranscriptMetrics], baseline_runs: list[TranscriptMetrics]) -> dict:
    """Median metrics for each arm plus pcg-vs-baseline reductions (lower is better)."""
    pcg = aggregate_runs(pcg_runs)
    base = aggregate_runs(baseline_runs)
    reductions = {
        f"{field_name}_reduction_pct": _pct_reduction(base[field_name], pcg[field_name])
        for field_name in _REDUCTION_FIELDS
    }
    return {"pcg": pcg, "baseline": base, "reductions": reductions}


def check_agent_ab_gate(
    payload: dict,
    *,
    min_token_reduction_pct: float = 0.0,
    min_tool_reduction_pct: float = 0.0,
) -> dict:
    """Dual-scorecard gate (Phase 1.5): fail if pcg loses to the baseline arm.

    For every flow, the pcg arm must reduce both total tokens and total tool calls vs
    the baseline arm by at least the given thresholds (default: simply not worse).
    Returns ``{"passed": bool, "failures": [...]}``.
    """
    failures: list[str] = []
    for flow in payload.get("flows", []):
        fid = flow.get("flow_id", "?")
        red = flow.get("reductions", {})
        tok = red.get("total_tokens_reduction_pct")
        tool = red.get("total_tool_calls_reduction_pct")
        if tok is not None and tok < min_token_reduction_pct:
            failures.append(f"{fid}: token reduction {tok}% < {min_token_reduction_pct}%")
        if tool is not None and tool < min_tool_reduction_pct:
            failures.append(f"{fid}: tool-call reduction {tool}% < {min_tool_reduction_pct}%")
        if flow.get("pcg", {}).get("errors", 0) > 0:
            failures.append(f"{fid}: pcg arm had {flow['pcg']['errors']} errored run(s)")
    return {"passed": not failures, "failures": failures}


def main(argv: list[str] | None = None) -> int:
    """Read a stream-json transcript from a file arg or stdin; print metrics JSON."""
    argv = argv if argv is not None else sys.argv[1:]
    if argv:
        with open(argv[0], encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    print(json.dumps(parse_stream_json(text).to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
