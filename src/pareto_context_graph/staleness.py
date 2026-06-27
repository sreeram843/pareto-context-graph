"""Stale search-index tracking, connect-time catch-up, and agent banners."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .indexing import (
    SEARCH_INDEX_STATUS_META,
    count_pending_index_files,
    list_pending_index_paths,
    update_search_indexes,
)
from .profiles import autodetect_profile
from .store import Store

_CATCHUP_DONE: set[str] = set()


def _catchup_enabled() -> bool:
    return os.environ.get("PCG_CATCHUP_ON_CONNECT", "1").lower() not in ("0", "false", "no")


def _catchup_max_files() -> int:
    return max(0, int(os.environ.get("PCG_CATCHUP_MAX_FILES", "50")))


def _watcher_disabled() -> bool:
    return os.environ.get("PCG_WATCH_DISABLED", "").lower() in ("1", "true", "yes")


def gather_staleness_report(
    store: Store,
    repo_root: Path,
    *,
    profile_name: str | None = None,
    sample_limit: int = 8,
) -> dict[str, Any]:
    profile = profile_name or autodetect_profile(repo_root)
    pending_count = count_pending_index_files(store, repo_root, profile_name=profile)
    pending_sample = list_pending_index_paths(
        store, repo_root, profile_name=profile, limit=sample_limit
    )
    search_status = store.get_meta(SEARCH_INDEX_STATUS_META) or "unknown"
    return {
        "pending_count": pending_count,
        "pending_sample": pending_sample,
        "search_index_status": search_status,
        "watcher_disabled": _watcher_disabled(),
        "fresh": pending_count == 0 and search_status != "pending",
    }


def format_staleness_banner(report: dict[str, Any]) -> str:
    if report.get("fresh"):
        return ""

    if report.get("watcher_disabled"):
        return (
            "⚠️ PCG auto-sync is DISABLED (PCG_WATCH_DISABLED). "
            "The search index may be stale — Read files directly to confirm recent edits.\n"
        )

    pending = int(report.get("pending_count") or 0)
    if pending <= 0:
        status = report.get("search_index_status")
        if status == "pending":
            return (
                "⚠️ PCG search index not built yet (lazy cold build). "
                "Run `pareto-context-graph index` or call `search` to resume indexing.\n"
            )
        return ""

    sample = report.get("pending_sample") or []
    extra = pending - len(sample)
    listed = ", ".join(sample[:8])
    if extra > 0:
        listed = f"{listed}, … (+{extra} more)"
    return (
        f"⚠️ PCG staleness: {pending} file(s) edited since last index sync "
        f"({listed}). Read those files directly for latest content; "
        f"co-change ranks are still valid.\n"
    )


def apply_staleness_to_text(json_text: str, banner: str) -> str:
    if not banner:
        return json_text
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return banner + json_text
    if isinstance(payload, dict) and "error" not in payload:
        payload.setdefault(
            "staleness",
            {
                "banner": banner.strip(),
                "pending_count": None,
            },
        )
        return banner + json.dumps(payload)
    return banner + json_text


def catch_up_on_connect(repo_root: Path, *, profile_name: str | None = None) -> dict[str, Any]:
    """Sync a bounded batch of pending index files once per server session."""
    key = str(repo_root.resolve())
    if key in _CATCHUP_DONE:
        return {"skipped": True, "reason": "already_done"}
    _CATCHUP_DONE.add(key)

    if not _catchup_enabled():
        return {"skipped": True, "reason": "disabled"}

    max_files = _catchup_max_files()
    if max_files <= 0:
        return {"skipped": True, "reason": "max_files_zero"}

    profile = profile_name or autodetect_profile(repo_root)
    store = Store(repo_root)
    try:
        pending = list_pending_index_paths(store, repo_root, profile_name=profile, limit=max_files)
        if not pending:
            return {"synced": 0, "pending_before": 0}
        stats = update_search_indexes(store, repo_root, paths=set(pending), profile_name=profile)
        remaining = count_pending_index_files(store, repo_root, profile_name=profile)
        return {
            "synced": stats.get("indexed", 0),
            "pending_before": len(pending),
            "pending_after": remaining,
            **{k: stats[k] for k in ("unchanged", "skipped") if k in stats},
        }
    finally:
        store.close()


def reset_catchup_state() -> None:
    """Test helper — clear per-session catch-up guard."""
    _CATCHUP_DONE.clear()
