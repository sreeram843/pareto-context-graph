from __future__ import annotations

from pareto_context_graph.graph import build_graph
from pareto_context_graph.store import DB_DIR, DB_NAME


def test_build_smoke_high_signal_pair(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=200, files=20, seed=11)

    store = build_graph(repo, max_commits=500)
    try:
        db_path = repo / DB_DIR / DB_NAME
        assert db_path.exists()

        neighbours = store.neighbours("src/a.py", min_weight=1)
        by_path = {path: weight for path, weight in neighbours}
        assert "src/b.py" in by_path
        assert by_path["src/b.py"] > 10
    finally:
        store.close()
