"""Compression stack metrics for eval — wraps payload_compress."""

from __future__ import annotations

from typing import Any

from .payload_compress import (
    COMPRESSION_METHOD,
    estimate_compressed_tokens,
    serialize_context_files,
)

# Legacy alias kept for docs/tests that referenced the old heuristic ratio.
DEFAULT_CODE_PAYLOAD_RATIO = 0.38


def compressed_tokens_from_row(row: dict[str, Any]) -> int | None:
    """Read compressed token count from eval row (new or legacy field)."""
    if row.get("compressed_tokens") is not None:
        return int(row["compressed_tokens"])
    if row.get("headroom_tokens") is not None:
        return int(row["headroom_tokens"])
    return None


def compression_method_from_row(row: dict[str, Any]) -> str:
    return str(row.get("compression_method") or row.get("headroom_method") or COMPRESSION_METHOD)


def build_compress_stack_block(
    response: dict[str, Any],
    *,
    graph_tokens: int | None = None,
    query: str = "",
    aggressive: bool = False,
) -> dict[str, Any]:
    """Attach compression savings fields to a tier-3 context response."""
    if response.get("content_hash") and response.get("tokens_before_compress") is not None:
        graph = int(response["tokens_before_compress"])
        compressed = int(response.get("tokens_used", 0))
        method = str(response.get("compression_method", COMPRESSION_METHOD))
        savings = float(response.get("compression_savings_ratio", 0.0))
    else:
        context_files = list(response.get("context_files") or [])
        serialized = serialize_context_files(context_files)
        graph = graph_tokens if graph_tokens is not None else int(response.get("tokens_used", 0))
        estimate = estimate_compressed_tokens(
            serialized,
            query=query,
            tokens_before=graph or None,
            aggressive=aggressive,
        )
        compressed = int(estimate["tokens_after"])
        method = str(estimate["method"])
        savings = float(estimate["savings_ratio"])

    corpus = (response.get("context_savings") or {}).get("naive_corpus_tokens", 0)
    return {
        "graph_tokens": graph,
        "compressed_tokens": compressed,
        "compressed_savings_ratio": round(savings, 4)
        if savings
        else (round(1.0 - (compressed / graph), 4) if graph else 0.0),
        "compression_method": method,
        "stack_reduction_vs_graph": round(graph / compressed, 2) if compressed else 0.0,
        "stack_reduction_vs_corpus": round(corpus / compressed, 2)
        if corpus and compressed
        else 0.0,
        "retrieve_command": "retrieve",
        "compress_command": "context",
        "compression_mode": response.get("compression", "prune"),
        "content_hash": response.get("content_hash"),
    }


def aggregate_compress_stack(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize graph tier-3 → compressed token columns across eval cases."""
    rows = [row for row in results if compressed_tokens_from_row(row) is not None]
    if not rows:
        return {
            "cases": 0,
            "mean_graph_tokens": 0.0,
            "mean_compressed_tokens": 0.0,
            "mean_stack_reduction_vs_graph": 0.0,
            "mean_compressed_savings_ratio": 0.0,
        }

    count = len(rows)

    def mean_graph() -> float:
        return round(sum(float(row.get("graph_tokens_tier3", 0)) for row in rows) / count, 2)

    def mean_compressed() -> float:
        return round(sum(float(compressed_tokens_from_row(row) or 0) for row in rows) / count, 2)

    def mean_key(key: str, legacy: str | None = None) -> float:
        total = 0.0
        for row in rows:
            total += float(row.get(key, row.get(legacy or "", 0.0)))
        return round(total / count, 2)

    return {
        "cases": count,
        "mean_graph_tokens": mean_graph(),
        "mean_compressed_tokens": mean_compressed(),
        "mean_stack_reduction_vs_graph": mean_key("stack_reduction_vs_graph"),
        "mean_compressed_savings_ratio": mean_key(
            "compressed_savings_ratio", "headroom_savings_ratio"
        ),
        "methods": sorted({compression_method_from_row(row) for row in rows}),
    }


def legacy_compress_stack_fields(block: dict[str, Any]) -> dict[str, Any]:
    """Mirror canonical compress fields under legacy headroom_* names."""
    return {
        "headroom_tokens": block["compressed_tokens"],
        "headroom_savings_ratio": block["compressed_savings_ratio"],
        "headroom_method": block["compression_method"],
    }


# Phase C gates — tier-3 tokens down, recall@5 unchanged (recall checked vs retrieval baseline).
MIN_STACK_REDUCTION = 1.05
MIN_MEAN_COMPRESSED_SAVINGS = 0.05
MIN_CASE_TOKEN_SAVINGS_FRACTION = 0.35
COMPRESS_REGRESSION_THRESHOLD = 0.05


def check_compress_stack_gate(result: dict[str, Any]) -> dict[str, Any]:
    """Sanity-check that prune compression reduces tier-3 tokens on real eval runs."""
    summary = result.get("summary") or {}
    compress = summary.get("compress_stack") or {}
    failures: list[dict[str, Any]] = []

    cases = int(compress.get("cases", 0))
    if cases <= 0:
        return {
            "passed": False,
            "failures": [{"check": "compress_stack_cases", "message": "no compress_stack rows"}],
        }

    graph_mean = float(compress.get("mean_graph_tokens", 0))
    compressed_mean = float(compress.get("mean_compressed_tokens", 0))
    if compressed_mean >= graph_mean:
        failures.append(
            {
                "check": "mean_tokens_down",
                "graph_tokens": graph_mean,
                "compressed_tokens": compressed_mean,
            }
        )

    reduction = float(compress.get("mean_stack_reduction_vs_graph", 0))
    if reduction < MIN_STACK_REDUCTION:
        failures.append(
            {
                "check": "min_stack_reduction",
                "value": reduction,
                "min": MIN_STACK_REDUCTION,
            }
        )

    savings = float(compress.get("mean_compressed_savings_ratio", 0))
    if savings < MIN_MEAN_COMPRESSED_SAVINGS:
        failures.append(
            {
                "check": "min_mean_savings",
                "value": savings,
                "min": MIN_MEAN_COMPRESSED_SAVINGS,
            }
        )

    rows = [row for row in result.get("results", []) if compressed_tokens_from_row(row) is not None]
    if rows:
        saved = sum(
            1
            for row in rows
            if (compressed_tokens_from_row(row) or 0) < int(row.get("graph_tokens_tier3", 0))
        )
        fraction = saved / len(rows)
        if fraction < MIN_CASE_TOKEN_SAVINGS_FRACTION:
            failures.append(
                {
                    "check": "per_case_savings_fraction",
                    "value": round(fraction, 4),
                    "min": MIN_CASE_TOKEN_SAVINGS_FRACTION,
                    "saved_cases": saved,
                    "total_cases": len(rows),
                }
            )

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "cases": cases,
        "mean_graph_tokens": graph_mean,
        "mean_compressed_tokens": compressed_mean,
        "mean_stack_reduction_vs_graph": reduction,
    }


def compare_compress_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    recall_threshold: float = 0.02,
    compress_threshold: float = COMPRESS_REGRESSION_THRESHOLD,
) -> dict[str, Any]:
    """Regression gate: recall@5 stable, compression metrics do not worsen vs baseline."""
    failures: list[dict[str, Any]] = []

    cur_summary = current.get("summary") or {}
    base_summary = baseline.get("summary") or {}
    cur_compress = cur_summary.get("compress_stack") or {}
    base_compress = base_summary.get("compress_stack") or {}

    cur_recall = float(cur_summary.get("mean_recall_at_5", 0))
    base_recall = float(base_summary.get("mean_recall_at_5", 0))
    if base_recall > 0 and cur_recall < base_recall - recall_threshold:
        failures.append(
            {
                "metric": "mean_recall_at_5",
                "baseline": base_recall,
                "current": cur_recall,
                "delta": round(cur_recall - base_recall, 4),
            }
        )

    cur_compressed = float(cur_compress.get("mean_compressed_tokens", 0))
    base_compressed = float(base_compress.get("mean_compressed_tokens", 0))
    if base_compressed > 0 and cur_compressed > base_compressed * (1 + compress_threshold):
        failures.append(
            {
                "metric": "mean_compressed_tokens",
                "baseline": base_compressed,
                "current": cur_compressed,
                "allowed_max": round(base_compressed * (1 + compress_threshold), 2),
            }
        )

    return {
        "passed": len(failures) == 0,
        "recall_threshold": recall_threshold,
        "compress_threshold": compress_threshold,
        "failures": failures,
    }


def portable_compress_baseline_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Minimal baseline file for Phase C compression regression gates."""
    summary = result.get("summary") or {}
    return {
        "summary": {
            "mean_recall_at_5": summary.get("mean_recall_at_5"),
            "compress_stack": summary.get("compress_stack"),
        }
    }
