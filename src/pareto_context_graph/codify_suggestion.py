"""Suggest codifying repeated feedback rejects into specs (Phase 15.7)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .feedback import NEGATIVE_KINDS, FeedbackEventLog

DEFAULT_MIN_REJECTS = 3
DEFAULT_SINCE_DAYS = 7


def reject_counts_by_path(
    repo_root: Path, *, since_days: int = DEFAULT_SINCE_DAYS
) -> dict[str, int]:
    """Count feedback reject events per path within a rolling window."""
    cutoff = int(time.time()) - max(1, since_days) * 86400
    counts: dict[str, int] = {}
    for event in FeedbackEventLog(repo_root).read_all():
        if str(event.get("kind", "")) not in NEGATIVE_KINDS:
            continue
        ts = int(event.get("ts", 0))
        if ts and ts < cutoff:
            continue
        path = str(event.get("path", ""))
        if path:
            counts[path] = counts.get(path, 0) + 1
    return counts


def build_codify_suggestions(
    repo_root: Path,
    paths: list[str],
    *,
    min_rejects: int = DEFAULT_MIN_REJECTS,
    since_days: int = DEFAULT_SINCE_DAYS,
) -> list[dict[str, Any]]:
    """Return codify nudges for paths rejected often in recent sessions."""
    counts = reject_counts_by_path(repo_root, since_days=since_days)
    suggestions: list[dict[str, Any]] = []
    for path in paths:
        reject_count = counts.get(path, 0)
        if reject_count < min_rejects:
            continue
        suggestions.append(
            {
                "path": path,
                "reject_count": reject_count,
                "window_days": since_days,
                "reason": f"rejected {reject_count}× in last {since_days} days",
                "hint": (
                    "Retrieval for this path is often wrong; add a .cursor rule, "
                    "docs snippet, or context-map entry for this area."
                ),
            }
        )
    suggestions.sort(key=lambda item: int(item["reject_count"]), reverse=True)
    return suggestions
