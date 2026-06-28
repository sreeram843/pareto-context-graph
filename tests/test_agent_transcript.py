"""Tests for the stream-json agent transcript parser (Phase 1.3 measurement core)."""

from __future__ import annotations

import json

from pareto_context_graph.agent_transcript import (
    aggregate_runs,
    arm_comparison,
    check_agent_ab_gate,
    classify_tool,
    parse_stream_json,
)


def _line(obj: dict) -> str:
    return json.dumps(obj)


# A synthetic claude -p --output-format stream-json transcript: two assistant turns
# (one pcg MCP call, several Read/Grep calls) and a terminal result summary.
BASELINE_ARM = "\n".join(
    [
        _line({"type": "system", "subtype": "init", "session_id": "x"}),
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "let me look"},
                        {"type": "tool_use", "name": "Grep", "input": {}},
                        {"type": "tool_use", "name": "Read", "input": {}},
                    ],
                    "usage": {"input_tokens": 1200, "output_tokens": 80},
                },
            }
        ),
        _line({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {}},
                        {"type": "tool_use", "name": "Read", "input": {}},
                        {"type": "tool_use", "name": "Bash", "input": {}},
                    ],
                    "usage": {"input_tokens": 3000, "output_tokens": 120},
                },
            }
        ),
        _line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 2,
                "duration_ms": 18540,
                "total_cost_usd": 0.0421,
                "result": "The request flows through app.py to handler.py.",
                "usage": {"input_tokens": 4200, "output_tokens": 200},
            }
        ),
    ]
)

PCG_ARM = "\n".join(
    [
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__pareto_context_graph__pareto_context_graph",
                            "input": {"command": "context"},
                        },
                        {"type": "tool_use", "name": "Read", "input": {}},
                    ],
                    "usage": {"input_tokens": 900, "output_tokens": 60},
                },
            }
        ),
        _line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "num_turns": 1,
                "duration_ms": 7200,
                "total_cost_usd": 0.0123,
                "result": "app.py -> handler.py -> response.py.",
                "usage": {"input_tokens": 1500, "output_tokens": 90},
            }
        ),
    ]
)


def test_classify_tool_buckets():
    assert classify_tool("mcp__pareto_context_graph__pareto_context_graph") == "pcg"
    assert classify_tool("mcp__pcg__context") == "pcg"
    assert classify_tool("Read") == "read"
    assert classify_tool("Grep") == "search"
    assert classify_tool("Bash") == "exec"
    assert classify_tool("Task") == "explore"
    assert classify_tool("WebFetch") == "other"


def test_baseline_arm_metrics():
    m = parse_stream_json(BASELINE_ARM)
    assert m.tool_calls["Read"] == 3
    assert m.tool_calls["Grep"] == 1
    assert m.tool_calls["Bash"] == 1
    assert m.tool_buckets["read"] == 3
    assert m.tool_buckets["search"] == 1
    assert m.total_tool_calls == 5
    assert m.tool_buckets.get("pcg", 0) == 0
    # Token/turn/cost come from the authoritative result summary, not the per-turn sum.
    assert m.input_tokens == 4200
    assert m.output_tokens == 200
    assert m.num_turns == 2
    assert m.duration_ms == 18540
    assert m.total_cost_usd == 0.0421
    assert not m.is_error
    assert "handler.py" in m.result_text


def test_pcg_arm_uses_fewer_reads_and_tokens():
    base = parse_stream_json(BASELINE_ARM)
    pcg = parse_stream_json(PCG_ARM)
    assert pcg.tool_buckets["pcg"] == 1
    assert pcg.tool_buckets["read"] < base.tool_buckets["read"]
    assert pcg.total_tokens < base.total_tokens
    assert pcg.num_turns < base.num_turns
    assert pcg.total_cost_usd < base.total_cost_usd


def test_fallback_to_per_turn_usage_when_no_result():
    # Transcript with no terminal result event: token totals fall back to the sum.
    stream = "\n".join(
        [
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "tool_use", "name": "Read", "input": {}}],
                        "usage": {"input_tokens": 500, "output_tokens": 40},
                    },
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [],
                        "usage": {"input_tokens": 700, "output_tokens": 30},
                    },
                }
            ),
        ]
    )
    m = parse_stream_json(stream)
    assert m.input_tokens == 1200
    assert m.output_tokens == 70
    assert m.tool_calls["Read"] == 1


def test_malformed_lines_are_skipped():
    stream = "not json\n" + _line({"type": "result", "num_turns": 1}) + "\n{bad"
    m = parse_stream_json(stream)
    assert m.num_turns == 1


def test_aggregate_runs_uses_median():
    runs = [parse_stream_json(PCG_ARM), parse_stream_json(BASELINE_ARM), parse_stream_json(PCG_ARM)]
    agg = aggregate_runs(runs)
    assert agg["runs"] == 3
    # median of [1, 2, 1] num_turns = 1
    assert agg["num_turns"] == 1
    # median read_calls of [1, 3, 1] = 1
    assert agg["read_calls"] == 1


def test_arm_comparison_reports_reductions():
    pcg = [parse_stream_json(PCG_ARM)] * 3
    base = [parse_stream_json(BASELINE_ARM)] * 3
    cmp = arm_comparison(pcg, base)
    # pcg uses fewer reads, tokens, turns, cost than baseline → positive reductions.
    assert cmp["reductions"]["read_calls_reduction_pct"] > 0
    assert cmp["reductions"]["total_tokens_reduction_pct"] > 0
    assert cmp["reductions"]["num_turns_reduction_pct"] > 0
    assert cmp["reductions"]["total_cost_usd_reduction_pct"] > 0
    assert cmp["pcg"]["pcg_calls"] >= 1


def test_reduction_none_when_baseline_zero():
    # No search calls in either arm → reduction is undefined (None), not a divide error.
    pcg = [parse_stream_json(PCG_ARM)]
    cmp = arm_comparison(pcg, pcg)
    assert cmp["reductions"]["search_calls_reduction_pct"] is None


def test_gate_passes_when_pcg_wins():
    cmp = arm_comparison([parse_stream_json(PCG_ARM)] * 3, [parse_stream_json(BASELINE_ARM)] * 3)
    payload = {"flows": [{"flow_id": "demo", **cmp}]}
    assert check_agent_ab_gate(payload)["passed"]


def test_gate_fails_when_pcg_regresses():
    # Swap arms: pcg arm is now the heavier baseline → negative reductions → gate fails.
    cmp = arm_comparison([parse_stream_json(BASELINE_ARM)] * 3, [parse_stream_json(PCG_ARM)] * 3)
    payload = {"flows": [{"flow_id": "demo", **cmp}]}
    result = check_agent_ab_gate(payload)
    assert not result["passed"]
    assert result["failures"]


def test_gate_flags_errored_pcg_runs():
    payload = {"flows": [{"flow_id": "demo", "pcg": {"errors": 2}, "reductions": {}}]}
    result = check_agent_ab_gate(payload)
    assert not result["passed"]
