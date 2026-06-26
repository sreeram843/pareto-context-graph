from __future__ import annotations

import threading
from pathlib import Path

from .graph import incremental_update
from .repo_caches import invalidate_caches


class GraphWatcher:
    """Periodic incremental updater for long-running serve sessions."""

    def __init__(self, repo_root: Path, interval: int = 600) -> None:
        self.repo_root = repo_root
        self.interval = max(1, int(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                store = incremental_update(self.repo_root)
                store.close()
                invalidate_caches()
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
