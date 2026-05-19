from __future__ import annotations

import time

from code_graph_mcp.daemon import GraphWatcher
from code_graph_mcp.graph import build_graph
from code_graph_mcp.store import Store


def test_graph_watcher_starts_and_stops(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=20, files=8, seed=4)
    store = build_graph(repo, max_commits=100)
    store.close()

    watcher = GraphWatcher(repo, interval=1)
    watcher.start()
    time.sleep(1.2)
    watcher.stop()

    # Still able to open store after watcher lifecycle.
    store = Store(repo)
    try:
        assert store.file_count() > 0
    finally:
        store.close()
