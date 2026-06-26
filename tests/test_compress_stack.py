"""Tests for compress_stack eval metrics."""

from __future__ import annotations

import json

from pareto_context_graph.compress_stack import (
    aggregate_compress_stack,
    build_compress_stack_block,
    check_compress_stack_gate,
    compare_compress_baseline,
    compressed_tokens_from_row,
    legacy_compress_stack_fields,
)
from pareto_context_graph.headroom_stack import (
    aggregate_headroom_stack,
    build_headroom_stack_block,
    estimate_headroom_tokens,
)
from pareto_context_graph.payload_compress import COMPRESSION_METHOD, serialize_context_files


def test_serialize_context_files_roundtrip():
    files = [{"path": "a.py", "chunks": [{"text": "def foo(): pass"}]}]
    text = serialize_context_files(files)
    assert json.loads(text) == files


def test_estimate_headroom_shim_uses_prune():
    text = "def authenticate():\n" + "    token = 1\n" * 100
    result = estimate_headroom_tokens(text, query="authenticate token")
    assert result["method"] == COMPRESSION_METHOD
    assert result["tokens_after"] < result["tokens_before"]


def test_build_compress_stack_block_from_live_response():
    response = {
        "tokens_used": 400,
        "tokens_before_compress": 1000,
        "content_hash": "abc",
        "compression_method": COMPRESSION_METHOD,
        "compression_savings_ratio": 0.6,
        "context_files": [{"path": "a.py", "content": "x"}],
    }
    block = build_compress_stack_block(response)
    assert block["graph_tokens"] == 1000
    assert block["compressed_tokens"] == 400
    assert block["compression_method"] == COMPRESSION_METHOD
    assert block["retrieve_command"] == "retrieve"


def test_build_compress_stack_block_estimates_when_no_hash():
    response = {
        "tokens_used": 1000,
        "context_files": [{"path": "a.py", "content": "print('hi')\n" * 50}],
        "context_savings": {"naive_corpus_tokens": 50000},
    }
    block = build_compress_stack_block(response, query="print")
    assert block["graph_tokens"] == 1000
    assert block["compressed_tokens"] < 1000


def test_legacy_fields_mirror_canonical():
    block = build_compress_stack_block(
        {"tokens_used": 500, "tokens_before_compress": 1000, "content_hash": "x",
         "compression_method": COMPRESSION_METHOD, "context_files": []},
    )
    legacy = legacy_compress_stack_fields(block)
    assert legacy["headroom_tokens"] == block["compressed_tokens"]
    assert legacy["headroom_method"] == block["compression_method"]


def test_headroom_shim_block_includes_legacy_keys():
    response = {
        "tokens_used": 1000,
        "context_files": [{"path": "a.py", "content": "print('hi')\n" * 50}],
    }
    block = build_headroom_stack_block(response, query="print")
    assert block["compressed_tokens"] == block["headroom_tokens"]
    assert block["compression_method"] == block["headroom_method"]


def test_aggregate_compress_stack_reads_legacy_rows():
    rows = [
        {
            "graph_tokens_tier3": 1000,
            "headroom_tokens": 400,
            "stack_reduction_vs_graph": 2.5,
            "headroom_savings_ratio": 0.6,
            "headroom_method": COMPRESSION_METHOD,
        },
        {
            "graph_tokens_tier3": 2000,
            "compressed_tokens": 800,
            "stack_reduction_vs_graph": 2.5,
            "compressed_savings_ratio": 0.6,
            "compression_method": COMPRESSION_METHOD,
        },
    ]
    summary = aggregate_compress_stack(rows)
    assert summary["cases"] == 2
    assert summary["mean_graph_tokens"] == 1500.0
    assert summary["mean_compressed_tokens"] == 600.0
    assert compressed_tokens_from_row(rows[0]) == 400


def test_aggregate_headroom_shim_adds_legacy_summary_keys():
    rows = [
        {
            "graph_tokens_tier3": 1000,
            "compressed_tokens": 400,
            "stack_reduction_vs_graph": 2.5,
            "compressed_savings_ratio": 0.6,
            "compression_method": COMPRESSION_METHOD,
        },
    ]
    summary = aggregate_headroom_stack(rows)
    assert summary["mean_headroom_tokens"] == summary["mean_compressed_tokens"]


def test_check_compress_stack_gate_pass_and_fail():
    good = {
        "summary": {
            "compress_stack": {
                "cases": 2,
                "mean_graph_tokens": 1000.0,
                "mean_compressed_tokens": 500.0,
                "mean_stack_reduction_vs_graph": 2.0,
                "mean_compressed_savings_ratio": 0.5,
            }
        },
        "results": [
            {"graph_tokens_tier3": 1000, "compressed_tokens": 400},
            {"graph_tokens_tier3": 1000, "compressed_tokens": 600},
        ],
    }
    assert check_compress_stack_gate(good)["passed"] is True

    bad = {
        "summary": {
            "compress_stack": {
                "cases": 2,
                "mean_graph_tokens": 500.0,
                "mean_compressed_tokens": 600.0,
                "mean_stack_reduction_vs_graph": 0.9,
                "mean_compressed_savings_ratio": 0.01,
            }
        },
        "results": [
            {"graph_tokens_tier3": 500, "compressed_tokens": 600},
            {"graph_tokens_tier3": 500, "compressed_tokens": 600},
        ],
    }
    report = check_compress_stack_gate(bad)
    assert report["passed"] is False
    assert len(report["failures"]) >= 2


def test_compare_compress_baseline_recall_and_tokens():
    baseline = {
        "summary": {
            "mean_recall_at_5": 0.8,
            "compress_stack": {
                "mean_compressed_tokens": 1000.0,
                "mean_stack_reduction_vs_graph": 1.5,
                "mean_compressed_savings_ratio": 0.25,
            },
        }
    }
    improved = {
        "summary": {
            "mean_recall_at_5": 0.82,
            "compress_stack": {
                "mean_compressed_tokens": 900.0,
                "mean_stack_reduction_vs_graph": 1.6,
                "mean_compressed_savings_ratio": 0.28,
            },
        }
    }
    worse_tokens = {
        "summary": {
            "mean_recall_at_5": 0.8,
            "compress_stack": {
                "mean_compressed_tokens": 1200.0,
                "mean_stack_reduction_vs_graph": 1.5,
                "mean_compressed_savings_ratio": 0.25,
            },
        }
    }
    worse_recall = {
        "summary": {
            "mean_recall_at_5": 0.7,
            "compress_stack": {
                "mean_compressed_tokens": 900.0,
                "mean_stack_reduction_vs_graph": 1.6,
                "mean_compressed_savings_ratio": 0.28,
            },
        }
    }

    assert compare_compress_baseline(improved, baseline)["passed"] is True
    assert compare_compress_baseline(worse_tokens, baseline)["passed"] is False
    assert compare_compress_baseline(worse_recall, baseline)["passed"] is False

    borderline_tokens = {
        "summary": {
            "mean_recall_at_5": 0.8,
            "compress_stack": {"mean_compressed_tokens": 1040.0},
        }
    }
    assert compare_compress_baseline(borderline_tokens, baseline)["passed"] is True
