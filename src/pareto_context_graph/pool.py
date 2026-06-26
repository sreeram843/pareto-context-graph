"""SQLite read pool for concurrent MCP serving (Phase 7.1)."""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .store import DB_DIR, DB_NAME, Store

_pools: dict[str, StorePool] = {}
_pools_lock = threading.Lock()


class StorePool:
    """Per-repo pool of read-only Store handles plus a single writer."""

    def __init__(self, repo_root: Path, *, pool_size: int | None = None) -> None:
        self.repo_root = Path(repo_root)
        self.db_path = self.repo_root / DB_DIR / DB_NAME
        cpu = os.cpu_count() or 4
        self.pool_size = max(1, min(32, pool_size or cpu * 2))
        self._read_queue: queue.Queue[Store] = queue.Queue(maxsize=self.pool_size)
        self._write_lock = threading.Lock()
        self._writer: Store | None = None
        self._closed = False
        self._ensure_db()

    def _ensure_db(self) -> None:
        if not self.db_path.exists():
            writer = Store(self.repo_root)
            writer.close()
        for _ in range(self.pool_size):
            self._read_queue.put(Store(self.repo_root, readonly=True))

    @contextmanager
    def read(self) -> Iterator[Store]:
        if self._closed:
            raise RuntimeError("StorePool is closed")
        store = self._read_queue.get()
        try:
            yield store
        finally:
            self._read_queue.put(store)

    @contextmanager
    def write(self) -> Iterator[Store]:
        if self._closed:
            raise RuntimeError("StorePool is closed")
        with self._write_lock:
            if self._writer is None:
                self._writer = Store(self.repo_root)
            yield self._writer

    def close(self) -> None:
        self._closed = True
        while not self._read_queue.empty():
            try:
                self._read_queue.get_nowait().close()
            except queue.Empty:
                break
        if self._writer is not None:
            self._writer.close()
            self._writer = None


def get_store_pool(repo_root: Path, *, pool_size: int | None = None) -> StorePool:
    key = str(Path(repo_root).resolve())
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None or pool._closed:
            pool = StorePool(Path(repo_root), pool_size=pool_size)
            _pools[key] = pool
        return pool


def close_store_pool(repo_root: Path) -> None:
    key = str(Path(repo_root).resolve())
    with _pools_lock:
        pool = _pools.pop(key, None)
    if pool is not None:
        pool.close()


@contextmanager
def open_store(repo_root: Path, *, write: bool = False, use_pool: bool = True) -> Iterator[Store]:
    """Acquire a Store from the pool when serving, else a standalone connection."""
    if not use_pool:
        store = Store(repo_root)
        try:
            yield store
        finally:
            store.close()
        return

    pool = get_store_pool(repo_root)
    if write:
        with pool.write() as store:
            yield store
    else:
        with pool.read() as store:
            yield store
