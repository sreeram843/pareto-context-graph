from __future__ import annotations

import json

from code_graph_mcp.graph import build_graph
from code_graph_mcp.server import _handle_tool_call


def test_context_returns_ranked_files_with_profile(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=120, files=20, seed=21)
    store = build_graph(repo, max_commits=500)
    store.close()

    out = _handle_tool_call(
        repo,
        "code_graph",
        {
            "command": "context",
            "files": ["src/a.py"],
            "query": "change auth logic",
            "profile": "huge",
            "tier": 1,
            "token_budget": 4000,
        },
    )
    payload = json.loads(out)
    assert payload["tier"] == 1
    assert payload["profile"] == "huge"
    assert isinstance(payload["context_files"], list)
