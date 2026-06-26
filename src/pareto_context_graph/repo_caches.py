"""Versioned in-process caches scoped to a repo graph generation."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from .store import DB_DIR

T = TypeVar("T")

DEFAULT_CACHE_TTL_SECONDS = float(os.environ.get("PCG_CACHE_TTL_SECONDS", "3600"))


@dataclass
class _CacheSlot(Generic[T]):
    repo_root: Path | None = None
    version: str | None = None
    value: T | None = None
    created_at: float = 0.0


_keyword_slot: _CacheSlot = _CacheSlot()
_degree_slot: _CacheSlot = _CacheSlot()
_learned_slot: _CacheSlot = _CacheSlot()


def graph_cache_version(repo_root: Path) -> str:
    db = repo_root / DB_DIR / "graph.db"
    if not db.is_file():
        return "missing"
    stat = db.stat()
    index_ver = ""
    try:
        from .store import Store

        store = Store(repo_root)
        try:
            index_ver = store.get_meta("search_index_version") or ""
            head = store.get_meta("last_commit_hash") or ""
        finally:
            store.close()
    except Exception:
        head = ""
    return f"{stat.st_mtime_ns}:{stat.st_size}:{index_ver}:{head}"


def get_repo_cached(
    slot: _CacheSlot[T],
    repo_root: Path,
    factory: Callable[[], T],
    *,
    ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    extra_version: str = "",
) -> T:
    version = graph_cache_version(repo_root) + extra_version
    now = time.monotonic()
    if (
        slot.value is not None
        and slot.repo_root == repo_root
        and slot.version == version
        and now - slot.created_at < ttl_seconds
    ):
        return slot.value
    slot.value = factory()
    slot.repo_root = repo_root
    slot.version = version
    slot.created_at = now
    return slot.value


def invalidate_caches() -> None:
    """Reset all in-process repo caches (call after graph build/update)."""
    global _keyword_slot, _degree_slot, _learned_slot
    _keyword_slot = _CacheSlot()
    _degree_slot = _CacheSlot()
    _learned_slot = _CacheSlot()
    from .prune_learn import invalidate_prune_weights_cache

    invalidate_prune_weights_cache()


def keyword_cache_slot() -> _CacheSlot:
    return _keyword_slot


def degree_cache_slot() -> _CacheSlot:
    return _degree_slot


def learned_weights_cache_slot() -> _CacheSlot:
    return _learned_slot
