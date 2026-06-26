from __future__ import annotations

from pareto_context_graph.store import Store


def test_bulk_co_change_matches_single_inserts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    single = Store(repo)
    try:
        single.record_co_change("a.py", "b.py", weight=2.0, last_seen_ts=100)
        single.record_co_change("a.py", "c.py", weight=3.0, last_seen_ts=200)
        single.record_co_change("b.py", "c.py", weight=1.0, last_seen_ts=150)
        single.commit()
        single_edges = {
            tuple(sorted((a, b))): w
            for a, b, w in single.conn.execute(
                """SELECT f1.path, f2.path, c.weight
                   FROM co_changes c
                   JOIN files f1 ON f1.id = c.file_a
                   JOIN files f2 ON f2.id = c.file_b"""
            ).fetchall()
        }
    finally:
        single.close()

    bulk = Store(repo / "bulk")
    try:
        bulk.record_co_changes_bulk(
            [
                ("a.py", "b.py", 2.0, 100),
                ("a.py", "c.py", 3.0, 200),
                ("b.py", "c.py", 1.0, 150),
            ]
        )
        bulk.commit()
        bulk_edges = {
            tuple(sorted((a, b))): w
            for a, b, w in bulk.conn.execute(
                """SELECT f1.path, f2.path, c.weight
                   FROM co_changes c
                   JOIN files f1 ON f1.id = c.file_a
                   JOIN files f2 ON f2.id = c.file_b"""
            ).fetchall()
        }
    finally:
        bulk.close()

    assert single_edges == bulk_edges
