from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from pareto_context_graph.bench import latency_summary
from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call
from tests.fixtures.build_repo import create_synthetic_repo


def run() -> None:
    root = Path(tempfile.mkdtemp())
    repo = create_synthetic_repo(root / "bench", commit_count=300, file_count=80)
    store = build_graph(repo, max_commits=500)
    store.close()

    queries = [
        "auth endpoint change",
        "test updates",
        "model service import",
        "routing handler",
        "middleware cors",
    ]

    times = []
    for query in queries:
        for _ in range(3):
            start = time.perf_counter()
            _handle_tool_call(
                repo,
                "pareto_context_graph",
                {
                    "command": "context",
                    "files": ["src/a.py"],
                    "query": query,
                    "tier": 1,
                    "token_budget": 5000,
                    "profile": "large",
                },
            )
            times.append(time.perf_counter() - start)

    print(json.dumps(latency_summary(times), indent=2))


if __name__ == "__main__":
    run()
