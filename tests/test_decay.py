from __future__ import annotations

import math
import time

from pareto_context_graph.store import Store


def test_apply_decay_prefers_recent_edge(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    try:
        now = int(time.time())
        ts_30 = now - (30 * 86400)
        ts_365 = now - (365 * 86400)

        store.record_co_change("a.py", "b.py", weight=10.0, last_seen_ts=ts_30)
        store.record_co_change("c.py", "d.py", weight=10.0, last_seen_ts=ts_365)
        store.commit()

        store.apply_decay(half_life_days=180)
        rows = store.conn.execute(
            """SELECT f1.path, f2.path, c.weight
               FROM co_changes c
               JOIN files f1 ON f1.id = c.file_a
               JOIN files f2 ON f2.id = c.file_b"""
        ).fetchall()
        by_pair = {(a, b): w for a, b, w in rows}

        newer = by_pair[("a.py", "b.py")]
        older = by_pair[("c.py", "d.py")]
        assert newer > older

        expected_ratio = math.exp(-30 / 180) / math.exp(-365 / 180)
        actual_ratio = newer / older
        assert abs(actual_ratio - expected_ratio) < 0.2
    finally:
        store.close()
