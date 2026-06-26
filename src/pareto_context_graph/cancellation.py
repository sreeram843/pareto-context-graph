"""MCP request cancellation registry (Phase 7.2)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def register(request_id: str) -> threading.Event:
    """Return a cancellation event for a new in-flight request."""
    event = threading.Event()
    with _lock:
        _events[request_id] = event
    return event


def cancel(request_id: str) -> bool:
    """Signal cancellation for request_id; returns True if a request was registered."""
    with _lock:
        event = _events.get(request_id)
    if event is None:
        return False
    event.set()
    return True


def clear(request_id: str) -> None:
    with _lock:
        _events.pop(request_id, None)


def is_cancelled(request_id: str) -> bool:
    with _lock:
        event = _events.get(request_id)
    return event is not None and event.is_set()
