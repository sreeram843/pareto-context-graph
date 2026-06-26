"""Unit tests for request deadline helpers."""

from __future__ import annotations

import time

from pareto_context_graph.deadlines import RequestDeadline, deadline_tick


def test_deadline_tick_interval() -> None:
    hits = [i for i in range(50) if deadline_tick(i)]
    assert hits == [0, 16, 32, 48]


def test_request_deadline_expires() -> None:
    deadline = RequestDeadline(timeout_ms=50)
    time.sleep(0.06)
    assert deadline.expired() is True
    assert deadline.remaining_ms() == 0.0
