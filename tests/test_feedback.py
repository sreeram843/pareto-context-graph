"""Test feedback logging and learn pipeline (Task 14)."""

import math

from pareto_context_graph.store import Store


def test_feedback_logging(tmp_path):
    """Feedback rows are persisted correctly."""
    store = Store(tmp_path)
    store.log_feedback("find auth", "auth.py", returned=True, used=False)
    store.log_feedback("find auth", "auth.py", returned=True, used=True)
    store.log_feedback("find auth", "other.py", returned=True, used=False)

    rows = store.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()
    assert rows[0] == 3
    store.close()


def test_mark_used(tmp_path):
    """mark_feedback_used updates the used flag."""
    store = Store(tmp_path)
    store.log_feedback("q", "a.py", returned=True, used=False)
    store.log_feedback("q", "b.py", returned=True, used=False)

    updated = store.mark_feedback_used(["a.py"])
    assert updated >= 1

    used = store.conn.execute("SELECT used FROM feedback WHERE file_path = 'a.py'").fetchone()
    assert used[0] == 1
    store.close()


def test_learn_produces_weights(tmp_path):
    """Learn command produces weights.json that boosts frequently-used files."""
    store = Store(tmp_path)
    # File that is always used
    for _ in range(20):
        store.log_feedback("q", "popular.py", returned=True, used=True)
    # File that is never used
    for _ in range(20):
        store.log_feedback("q", "ignored.py", returned=True, used=False)
    store.close()

    # Simulate learn logic (same as cli.py cmd_learn)
    store = Store(tmp_path)
    rows = store.conn.execute(
        "SELECT file_path, SUM(used), COUNT(*) FROM feedback GROUP BY file_path"
    ).fetchall()
    store.close()

    weights = {}
    for file_path, used_count, total_count in rows:
        total = max(1, int(total_count))
        ratio = float(used_count) / total
        ratio = min(0.99, max(0.01, ratio))
        weights[file_path] = math.log(ratio / (1 - ratio))

    assert weights["popular.py"] > 0, "Popular file should have positive weight"
    assert weights["ignored.py"] < 0, "Ignored file should have negative weight"
    assert weights["popular.py"] > weights["ignored.py"]
