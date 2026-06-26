"""Pluggable token counting for honest context budgets."""

from __future__ import annotations

import os
from typing import Protocol

from .tokens import BYTES_PER_TOKEN


class Tokenizer(Protocol):
    """Count tokens in text using a client-aligned encoding."""

    name: str

    def count(self, text: str) -> int: ...


class BytesPerTokenTokenizer:
    """Legacy bytes-per-token heuristic (zero dependencies)."""

    name = "legacy"

    def __init__(self, bytes_per_token: int = BYTES_PER_TOKEN) -> None:
        self.bytes_per_token = max(1, bytes_per_token)

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(0, len(text.encode("utf-8")) // self.bytes_per_token)


class TiktokenTokenizer:
    """OpenAI tiktoken encodings (cl100k_base, o200k_base, ...)."""

    def __init__(self, encoding: str = "cl100k_base") -> None:
        try:
            import tiktoken
        except ImportError as exc:
            raise ImportError(
                "tiktoken is not installed. Install with: pip install 'pareto-context-graph[tiktoken]'"
            ) from exc
        self._encoding = tiktoken.get_encoding(encoding)
        self.name = f"tiktoken:{encoding}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text))


def resolve_tokenizer(request_tokenizer: str | None = None) -> Tokenizer:
    """Select tokenizer from request arg, PCG_TOKENIZER env, or auto default."""
    name = (request_tokenizer or os.environ.get("PCG_TOKENIZER") or "auto").strip().lower()

    if name in ("legacy", "bytes", "bytes_per_token"):
        return BytesPerTokenTokenizer()

    if name == "auto":
        try:
            return TiktokenTokenizer("cl100k_base")
        except ImportError:
            return BytesPerTokenTokenizer()

    if name in ("cl100k_base", "o200k_base"):
        return TiktokenTokenizer(name)

    if name.startswith("tiktoken:"):
        encoding = name.split(":", 1)[1]
        return TiktokenTokenizer(encoding)

    raise ValueError(
        f"Unknown tokenizer {name!r}. Use legacy, cl100k_base, o200k_base, or tiktoken:<encoding>."
    )


def count_json(tokenizer: Tokenizer, payload: object) -> int:
    """Count tokens for a JSON-serializable payload."""
    import json

    return tokenizer.count(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
