"""SWE-Pruner-style post-pack pruning for tier-1 summaries (Phase 11.4)."""

from __future__ import annotations

import re
from typing import Any

from .payload_compress import query_terms

_KEEP_SIGNALS = frozenset({"query_first", "semantic", "orchestrator", "rwr"})
_PATH_SPLIT_RE = re.compile(r"[/_.\\-]+")


def _path_term_hits(path: str, terms: set[str]) -> int:
    lower = path.lower()
    hits = sum(1 for term in terms if term in lower)
    parts = _PATH_SPLIT_RE.split(lower)
    hits += sum(1 for term in terms if term in parts)
    return hits


def _summary_term_hits(summary: str, terms: set[str]) -> int:
    lower = summary.lower()
    return sum(1 for term in terms if term in lower)


def tier1_entry_matches_query(
    entry: dict[str, Any],
    *,
    query_terms_set: set[str],
    seed_files: set[str],
) -> bool:
    """True when a tier-1 row should be kept after summary prune."""
    if not query_terms_set:
        return True

    path = str(entry.get("path", ""))
    if path in seed_files:
        return True

    signal = str(entry.get("signal", ""))
    if signal in _KEEP_SIGNALS:
        return True

    summary = str(entry.get("summary", ""))
    score = _path_term_hits(path, query_terms_set) + _summary_term_hits(summary, query_terms_set)
    return score > 0


def apply_summary_prune(
    context_files: list[dict[str, Any]],
    *,
    query: str,
    tier: int,
    seed_files: list[str] | None = None,
    min_keep: int = 3,
    protect_top: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop tier-1 rows whose path/summary do not match query terms.

    Preserves seed files, retrieval signals, the first ``protect_top`` ranked rows,
    and at least ``min_keep`` rows (by original rank order).
    """
    if tier != 1 or not query.strip():
        return context_files, {}

    terms = query_terms(query)
    if not terms:
        return context_files, {}

    seeds = set(seed_files or [])
    protected = context_files[: max(0, min(protect_top, len(context_files)))]
    tail = context_files[len(protected) :]
    kept_tail: list[dict[str, Any]] = []
    dropped_paths: list[str] = []

    for entry in tail:
        if tier1_entry_matches_query(entry, query_terms_set=terms, seed_files=seeds):
            kept_tail.append(entry)
        else:
            dropped_paths.append(str(entry.get("path", "")))

    kept = list(protected) + kept_tail
    if len(kept) < min_keep and len(context_files) > len(kept):
        kept_paths = {str(entry.get("path", "")) for entry in kept}
        for entry in context_files:
            if len(kept) >= min_keep:
                break
            path = str(entry.get("path", ""))
            if path not in kept_paths:
                kept.append(entry)
                kept_paths.add(path)
                if path in dropped_paths:
                    dropped_paths.remove(path)

    meta: dict[str, Any] = {
        "dropped_count": len(dropped_paths),
        "kept_count": len(kept),
        "protected_top": len(protected),
        "query_terms": sorted(terms)[:20],
    }
    if dropped_paths:
        meta["dropped_paths"] = dropped_paths[:15]
    return kept, meta
