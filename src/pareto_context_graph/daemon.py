from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from .graph import incremental_update
from .indexing import update_search_indexes
from .metrics import METRICS
from .profiles import autodetect_profile
from .repo_caches import invalidate_caches
from .store import Store
from .watcher_health import mark_error, mark_started, mark_sync

logger = logging.getLogger(__name__)

DEFAULT_DEBOUNCE_MS = max(100, int(os.environ.get("PCG_WATCH_DEBOUNCE_MS", "2000")))
DEFAULT_POLL_MS = max(250, int(os.environ.get("PCG_WATCH_POLL_MS", "1000")))
DEFAULT_COCHANGE_INTERVAL = max(30, int(os.environ.get("PCG_COCHANGE_INTERVAL", "600")))


def _git_head(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return out
    except (OSError, subprocess.CalledProcessError):
        return ""


def _normalize_path(repo_root: Path, path: Path | str) -> str | None:
    try:
        rel = Path(path).resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    text = rel.as_posix()
    if text.startswith(".pareto-context-graph/"):
        return None
    return text


def _record_watcher_error(phase: str, exc: Exception) -> None:
    message = f"{phase}: {exc}"
    logger.warning("GraphWatcher %s", message)
    mark_error(message)
    METRICS.inc("cgmcp_watcher_errors_total", phase=phase)


class GraphWatcher:
    """Debounced search-index sync plus periodic co-change incremental updates."""

    def __init__(
        self,
        repo_root: Path,
        *,
        interval: int | None = None,
        debounce_ms: int | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.cochange_interval = max(30, int(interval or DEFAULT_COCHANGE_INTERVAL))
        self.debounce_ms = max(100, int(debounce_ms or DEFAULT_DEBOUNCE_MS))
        self._stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._cochange_thread: threading.Thread | None = None
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self._debounce_deadline = 0.0
        self._use_watchfiles = False

    def start(self) -> None:
        if os.environ.get("PCG_WATCH_DISABLED", "").lower() in ("1", "true", "yes"):
            return
        if self._watch_thread and self._watch_thread.is_alive():
            return
        try:
            import watchfiles  # noqa: F401

            self._use_watchfiles = True
            self._watch_thread = threading.Thread(target=self._watchfiles_loop, daemon=True)
            backend = "watchfiles"
        except ImportError:
            self._use_watchfiles = False
            self._watch_thread = threading.Thread(target=self._poll_loop, daemon=True)
            backend = "poll"
        mark_started(backend=backend)
        self._watch_thread.start()
        self._cochange_thread = threading.Thread(target=self._cochange_loop, daemon=True)
        self._cochange_thread.start()

    def _queue_paths(self, paths: set[str]) -> None:
        if not paths:
            return
        with self._pending_lock:
            self._pending.update(paths)
            self._debounce_deadline = time.monotonic() + (self.debounce_ms / 1000.0)

    def _drain_pending(self) -> set[str]:
        with self._pending_lock:
            paths = set(self._pending)
            self._pending.clear()
        return paths

    def _flush_if_due(self) -> bool:
        with self._pending_lock:
            if not self._pending:
                return False
            if time.monotonic() < self._debounce_deadline:
                return False
        paths = self._drain_pending()
        if paths:
            self._sync_search_index(paths)
        return True

    def _sync_search_index(self, paths: set[str]) -> None:
        try:
            profile = autodetect_profile(self.repo_root)
            store = Store(self.repo_root)
            try:
                update_search_indexes(store, self.repo_root, paths=paths, profile_name=profile)
            finally:
                store.close()
            invalidate_caches()
            mark_sync(len(paths))
        except Exception as exc:
            _record_watcher_error("search_index", exc)

    def _scan_pending_paths(self) -> set[str]:
        from .indexing import list_pending_index_paths

        try:
            store = Store(self.repo_root, readonly=True)
            try:
                return set(list_pending_index_paths(store, self.repo_root))
            finally:
                store.close()
        except Exception as exc:
            _record_watcher_error("scan", exc)
            return set()

    def _watchfiles_loop(self) -> None:
        from watchfiles import Change, watch

        while not self._stop.is_set():
            try:
                for changes in watch(
                    self.repo_root,
                    debounce=self.debounce_ms,
                    step=200,
                    stop_event=self._stop,
                ):
                    paths: set[str] = set()
                    for change, raw in changes:
                        if change not in (Change.added, Change.modified):
                            continue
                        norm = _normalize_path(self.repo_root, raw)
                        if norm:
                            paths.add(norm)
                    if paths:
                        self._sync_search_index(paths)
            except Exception as exc:
                _record_watcher_error("watchfiles", exc)
                if self._stop.wait(1):
                    break

    def _poll_loop(self) -> None:
        poll_s = DEFAULT_POLL_MS / 1000.0
        while not self._stop.is_set():
            changed = self._scan_pending_paths()
            if changed:
                self._queue_paths(changed)
            self._flush_if_due()
            if self._stop.wait(poll_s):
                break
        self._flush_if_due()

    def _cochange_loop(self) -> None:
        last_head = _git_head(self.repo_root)
        while not self._stop.is_set():
            if self._stop.wait(self.cochange_interval):
                break
            head = _git_head(self.repo_root)
            if not head or head == last_head:
                continue
            try:
                store = incremental_update(self.repo_root)
                store.close()
                invalidate_caches()
                last_head = head
            except Exception as exc:
                _record_watcher_error("cochange", exc)

    def stop(self) -> None:
        self._stop.set()
        for thread in (self._watch_thread, self._cochange_thread):
            if thread:
                thread.join(timeout=3)
