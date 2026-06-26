from __future__ import annotations

import tempfile
import time
from pathlib import Path

from pareto_context_graph.graph import build_graph_sharded
from tests.fixtures.build_repo import create_synthetic_repo


def run() -> None:
    root = Path(tempfile.mkdtemp())
    sizes = {
        "tiny": (50, 20),
        "medium": (500, 120),
        "large": (2000, 200),
    }

    for name, (commits, files) in sizes.items():
        repo = create_synthetic_repo(root / name, commit_count=commits, file_count=files)
        start = time.perf_counter()
        store = build_graph_sharded(
            repo, max_commits=commits + 50, shards=2 if name == "large" else 1
        )
        store.close()
        elapsed = time.perf_counter() - start
        print(f"{name}: build {elapsed:.3f}s")


if __name__ == "__main__":
    run()
