"""Deprecated shim — use compress_stack.py instead."""

from __future__ import annotations

from typing import Any

from .compress_stack import (
    DEFAULT_CODE_PAYLOAD_RATIO,
    aggregate_compress_stack,
    build_compress_stack_block,
    legacy_compress_stack_fields,
)
from .payload_compress import estimate_compressed_tokens

__all__ = [
    "DEFAULT_CODE_PAYLOAD_RATIO",
    "aggregate_headroom_stack",
    "build_headroom_stack_block",
    "estimate_headroom_tokens",
    "headroom_available",
]


def headroom_available() -> bool:
    return False


def estimate_headroom_tokens(
    text: str,
    *,
    query: str = "",
    tokens_before: int | None = None,
    aggressive: bool = False,
) -> dict[str, Any]:
    return estimate_compressed_tokens(
        text,
        query=query,
        tokens_before=tokens_before,
        aggressive=aggressive,
    )


def build_headroom_stack_block(
    response: dict[str, Any],
    *,
    graph_tokens: int | None = None,
    query: str = "",
) -> dict[str, Any]:
    block = build_compress_stack_block(response, graph_tokens=graph_tokens, query=query)
    return {**block, **legacy_compress_stack_fields(block)}


def aggregate_headroom_stack(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = aggregate_compress_stack(results)
    return {
        **summary,
        "mean_headroom_tokens": summary["mean_compressed_tokens"],
        "mean_headroom_savings_ratio": summary["mean_compressed_savings_ratio"],
    }
