"""In-house context payload compression — query-aware prune + verbatim retrieve."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .tokenizer import Tokenizer, count_json

PAYLOAD_CACHE_DIR = "payload_cache"
COMPRESSION_METHOD = "prune_v1"

_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "is",
        "are",
        "was",
        "were",
        "be",
        "with",
        "from",
        "by",
        "as",
        "it",
        "this",
        "that",
        "how",
        "what",
        "when",
        "where",
        "why",
        "do",
        "does",
        "did",
        "can",
        "should",
        "would",
        "fix",
        "add",
        "get",
        "set",
        "use",
        "file",
        "files",
        "code",
        "function",
        "class",
    }
)

_KEEP_LINE_RE = re.compile(
    r"(?:^\s*(?:def|class|async def|import|from|raise|assert|return|except|try|if __name__)\b"
    r"|(?:Error|Exception|TODO|FIXME|NotImplemented))",
    re.IGNORECASE,
)


def _cache_dir(repo_root: Path) -> Path:
    return repo_root / ".pareto-context-graph" / PAYLOAD_CACHE_DIR


def _query_terms(query: str) -> set[str]:
    terms = {t for t in re.split(r"\W+", query.lower()) if len(t) > 2}
    return {t for t in terms if t not in _QUERY_STOPWORDS}


def query_terms(query: str) -> set[str]:
    """Public wrapper for query term extraction (summary prune, tests)."""
    return _query_terms(query)


def _line_score(line: str, query_terms: set[str], *, keep_bias: float = 0.0) -> int:
    lower = line.lower()
    score = sum(2 for term in query_terms if term in lower)
    if _KEEP_LINE_RE.search(line):
        score += 3
    if line.strip().startswith(("#", "//", "/*", "*", '"""', "'''")):
        score += 1
    if keep_bias > 0:
        score += int(round(keep_bias * 2))
    elif keep_bias < 0:
        score -= int(round(abs(keep_bias)))
    return score


def prune_body(
    body: str,
    query: str,
    *,
    aggressive: bool = False,
    keep_bias: float = 0.0,
) -> str:
    """Trim a code body to query-relevant lines while keeping structure."""
    if not body or not body.strip():
        return body

    lines = body.splitlines()
    if len(lines) <= 3:
        return body

    query_terms = _query_terms(query)
    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        scored.append((idx, _line_score(line, query_terms, keep_bias=keep_bias), line))

    # Always keep the first line (usually a signature / block opener).
    keep_indices: set[int] = {0}
    if len(lines) > 1 and lines[1].strip():
        keep_indices.add(1)

    positive = [item for item in scored if item[1] > 0]
    if not positive and not query_terms:
        max_lines = 25 if aggressive else 40
        max_lines = int(max_lines * (1.0 + max(0.0, keep_bias) * 0.25))
        return "\n".join(lines[:max_lines])

    ranked = sorted(positive, key=lambda item: (-item[1], item[0]))
    effective_aggressive = aggressive or keep_bias < -0.5
    ratio = 0.35 if effective_aggressive else 0.55
    ratio = min(0.9, max(0.2, ratio + keep_bias * 0.12))
    max_keep = max(4, int(len(lines) * ratio))
    max_keep = min(max_keep, 30 if aggressive else 50)

    for idx, score, _line in ranked[:max_keep]:
        keep_indices.add(idx)

    if aggressive:
        for idx, score, _line in ranked:
            if score >= 5:
                keep_indices.add(idx)

    kept = [lines[i] for i in sorted(keep_indices) if i < len(lines)]
    if len(kept) >= len(lines):
        return body
    kept.append("# ... pruned (use retrieve with content_hash for full text)")
    return "\n".join(kept)


def _prune_chunks(
    chunks: list[dict[str, Any]],
    query: str,
    *,
    aggressive: bool,
    keep_bias: float = 0.0,
) -> list[dict[str, Any]]:
    if not chunks:
        return chunks

    query_terms = _query_terms(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for chunk in chunks:
        name = str(chunk.get("name", ""))
        body = str(chunk.get("body", ""))
        text = (name + " " + body).lower()
        score = sum(3 for term in query_terms if term in text)
        if "class" in name.lower() or "def " in body[:80].lower():
            score += 2
        scored.append((score, dict(chunk)))

    scored.sort(key=lambda item: -item[0])
    effective_aggressive = aggressive or keep_bias < -0.5
    max_chunks = 2 if effective_aggressive else 4
    if keep_bias > 0.5:
        max_chunks += 1
    if not any(score > 0 for score, _ in scored):
        selected = [chunk for _, chunk in scored[:max_chunks]]
    else:
        selected = [chunk for score, chunk in scored if score > 0][:max_chunks]
        if not selected:
            selected = [chunk for _, chunk in scored[:1]]

    pruned: list[dict[str, Any]] = []
    for chunk in selected:
        body = str(chunk.get("body", ""))
        chunk = dict(chunk)
        chunk["body"] = prune_body(
            body, query, aggressive=effective_aggressive, keep_bias=keep_bias
        )
        pruned.append(chunk)
    return pruned


def prune_context_entry(
    entry: dict[str, Any],
    query: str,
    *,
    aggressive: bool = False,
    keep_bias: float = 0.0,
) -> dict[str, Any]:
    """Return a pruned copy of one context file entry."""
    effective_aggressive = aggressive or keep_bias < -0.5
    out = dict(entry)
    chunks = out.get("chunks")
    if isinstance(chunks, list) and chunks:
        out["chunks"] = _prune_chunks(
            chunks, query, aggressive=effective_aggressive, keep_bias=keep_bias
        )
        return out

    content = out.get("content")
    if isinstance(content, str) and content:
        out["content"] = prune_body(
            content, query, aggressive=effective_aggressive, keep_bias=keep_bias
        )
        return out

    summary = out.get("summary")
    if isinstance(summary, str) and summary:
        sentences = re.split(r"(?<=[.!?])\s+", summary.strip())
        query_terms = _query_terms(query)
        kept = [sentences[0]] if sentences else []
        for sent in sentences[1:]:
            lower = sent.lower()
            if any(term in lower for term in query_terms):
                kept.append(sent)
        if aggressive:
            kept = kept[:2]
        elif len(kept) < len(sentences):
            kept.append("…")
        out["summary"] = " ".join(kept)
        return out

    signatures = out.get("signatures")
    if isinstance(signatures, list) and signatures and effective_aggressive:
        query_terms = _query_terms(query)
        ranked = sorted(
            signatures,
            key=lambda sig: sum(1 for term in query_terms if term in sig.lower()),
            reverse=True,
        )
        out["signatures"] = ranked[:10] if ranked else signatures[:10]
    return out


def prune_context_files(
    context_files: list[dict[str, Any]],
    query: str,
    *,
    aggressive: bool = False,
    prune_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    weights = prune_weights or {}
    return [
        prune_context_entry(
            entry,
            query,
            aggressive=aggressive,
            keep_bias=float(weights.get(str(entry.get("path", "")), 0.0)),
        )
        for entry in context_files
    ]


def serialize_context_files(context_files: list[dict[str, Any]]) -> str:
    """Serialize context entries the way an agent consumes them."""
    return json.dumps(context_files, separators=(",", ":"), ensure_ascii=False)


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def store_payload(repo_root: Path, payload: dict[str, Any]) -> str:
    """Persist a payload under ``.pareto-context-graph/payload_cache/<sha256>.json``."""
    raw = _canonical_payload(payload)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cache_path = _cache_dir(repo_root) / f"{digest}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        cache_path.write_text(raw, encoding="utf-8")
    return digest


def retrieve_payload(repo_root: Path, content_hash: str) -> dict[str, Any] | None:
    """Load a cached payload by SHA-256 hex digest."""
    if not re.fullmatch(r"[a-f0-9]{64}", content_hash):
        return None
    cache_path = _cache_dir(repo_root) / f"{content_hash}.json"
    if not cache_path.is_file():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def compress_context_payload(
    context_files: list[dict[str, Any]],
    query: str,
    *,
    aggressive: bool = False,
    prune_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Prune context file entries in place (returns new list)."""
    return prune_context_files(
        context_files, query, aggressive=aggressive, prune_weights=prune_weights
    )


def apply_context_compression(
    response: dict[str, Any],
    *,
    repo_root: Path,
    query: str,
    compression: str,
    tokenizer: Tokenizer,
) -> dict[str, Any]:
    """Cache the pre-compression payload, prune when requested, refresh token counts."""
    if compression not in ("prune", "aggressive"):
        return response

    context_files = list(response.get("context_files") or [])
    if not context_files:
        return response

    aggressive = compression == "aggressive"
    from .prune_learn import load_prune_weights

    prune_weights = load_prune_weights(repo_root)
    learned = bool(prune_weights)
    cache_payload = {
        "context_files": context_files,
        "tier": response.get("tier"),
        "query": query,
        "compression": "none",
    }
    content_hash = store_payload(repo_root, cache_payload)
    tokens_before = int(response.get("tokens_used", 0))

    pruned_files = compress_context_payload(
        context_files, query, aggressive=aggressive, prune_weights=prune_weights
    )
    tokens_after = 0
    for entry in pruned_files:
        entry_tokens = count_json(tokenizer, entry)
        entry["tokens_actual"] = entry_tokens
        tokens_after += entry_tokens

    if tokens_after >= tokens_before:
        return response

    out = dict(response)
    out["context_files"] = pruned_files
    out["tokens_used"] = tokens_after
    out["tokens_before_compress"] = tokens_before
    out["content_hash"] = content_hash
    out["compression"] = compression
    out["compression_method"] = COMPRESSION_METHOD
    if learned:
        out["learned_prune"] = True
        out["learned_prune_paths"] = sum(
            1 for entry in context_files if str(entry.get("path", "")) in prune_weights
        )
    if tokens_before > 0:
        out["compression_savings_ratio"] = round(1.0 - (tokens_after / tokens_before), 4)
    out["retrieve_command"] = "retrieve"
    return out


def estimate_compressed_tokens(
    text: str,
    *,
    query: str = "",
    tokens_before: int | None = None,
    aggressive: bool = False,
    tokenizer: Tokenizer | None = None,
) -> dict[str, Any]:
    """Estimate tokens after in-house prune compression (for eval)."""
    from .tokenizer import resolve_tokenizer

    tok = tokenizer or resolve_tokenizer(None)
    before = tokens_before
    if before is None:
        before = tok.count(text)

    try:
        files = json.loads(text)
        if not isinstance(files, list):
            files = [{"content": text}]
    except json.JSONDecodeError:
        files = [{"content": text}]

    pruned = compress_context_payload(files, query, aggressive=aggressive)
    after = sum(count_json(tok, entry) for entry in pruned)
    after = max(1, after)
    return {
        "tokens_before": before,
        "tokens_after": after,
        "savings_ratio": round(1.0 - (after / before), 4) if before else 0.0,
        "method": COMPRESSION_METHOD,
    }
