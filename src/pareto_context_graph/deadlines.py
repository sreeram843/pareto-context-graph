"""Per-request deadlines for the context pipeline (Phase 7.2)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

_tls = threading.local()


def set_current_cancel_event(event: threading.Event | None) -> None:
    _tls.event = event


def current_cancel_event() -> threading.Event | None:
    return getattr(_tls, "event", None)


def clear_current_cancel_event() -> None:
    _tls.event = None


DEFAULT_TIMEOUT_MS = 5000
MAX_SYMBOL_FILE_READS = 200
DEADLINE_TICK_INTERVAL = 16
MEGA_HUB_DEGREE_THRESHOLD = 2000
MEGA_HUB_STAGE1_CAP = 75
HIGH_FANOUT_DEGREE_THRESHOLD = 500
LARGE_REPO_GRAPH_FILES = 10_000


def deadline_tick(counter: int, *, interval: int = DEADLINE_TICK_INTERVAL) -> bool:
    """True when callers should check the request deadline."""
    return (counter % interval) == 0


class PhaseTimeout(Exception):
    """Raised when a context pipeline phase exceeds the request deadline."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"context deadline exceeded during {phase}")


@dataclass
class RequestDeadline:
    """Monotonic deadline shared across context phases."""

    timeout_ms: int = DEFAULT_TIMEOUT_MS
    cancel_event: threading.Event | None = field(default=None, repr=False)
    _end: float = field(init=False)
    timed_out_phase: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.timeout_ms = max(1, int(self.timeout_ms))
        self._end = time.monotonic() + (self.timeout_ms / 1000.0)

    def expired(self) -> bool:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        return time.monotonic() >= self._end

    def remaining_ms(self) -> float:
        return max(0.0, (self._end - time.monotonic()) * 1000.0)

    def check(self, phase: str) -> None:
        if self.expired():
            self.timed_out_phase = phase
            raise PhaseTimeout(phase)

    def mark_timeout(self, phase: str) -> None:
        self.timed_out_phase = phase
