"""In-process watcher health for doctor and metrics (#20)."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "enabled": False,
    "backend": "none",
    "last_sync_ts": None,
    "last_sync_paths": 0,
    "last_error": None,
    "last_error_ts": None,
    "error_count": 0,
}


def mark_started(*, backend: str) -> None:
    with _lock:
        _state["enabled"] = True
        _state["backend"] = backend


def mark_sync(paths: int) -> None:
    with _lock:
        _state["last_sync_ts"] = int(time.time())
        _state["last_sync_paths"] = max(0, int(paths))


def mark_error(message: str) -> None:
    with _lock:
        _state["last_error"] = message[:500]
        _state["last_error_ts"] = int(time.time())
        _state["error_count"] = int(_state.get("error_count") or 0) + 1


def snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def reset_for_tests() -> None:
    with _lock:
        _state.update(
            {
                "enabled": False,
                "backend": "none",
                "last_sync_ts": None,
                "last_sync_paths": 0,
                "last_error": None,
                "last_error_ts": None,
                "error_count": 0,
            }
        )
