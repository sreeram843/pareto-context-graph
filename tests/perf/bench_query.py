from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from tests.fixtures.build_repo import create_synthetic_repo
from code_graph_mcp.graph import build_graph
from code_graph_mcp.server import _handle_tool_call


def run() -> None:
    root = Path(tempfile.mkdtemp())
    repo = create_synthetic_repo(root / "bench", commit_count=300, file_count=80)
    store = build_graph(repo, max_commits=500)
    store.close()

    queries = [
        "auth endpoint change",
        "test updates",
        "model service import",
    ]

    times = []
    for query in queries:
        start = time.perf_counter()
        _handle_tool_call(
            repo,
            "code_graph",
            {
                "command": "context",
                "files": ["src/a.py"],
                "query": query,
                "tier": 1,
                "token_budget": 5000,
            },
        )
        times.append(time.perf_counter() - start)

    times.sort()
    p50 = times[len(times) // 2]
    p95 = times[-1]
    print(json.dumps({"p50": p50, "p95": p95, "samples": len(times)}, indent=2))


if __name__ == "__main__":
    run()
