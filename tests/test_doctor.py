from __future__ import annotations

import json

from code_graph_mcp.graph import build_graph
from code_graph_mcp.server import _handle_tool_call


def test_doctor_command_returns_expected_keys(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=5)
    store = build_graph(repo, max_commits=100)
    store.close()

    payload = json.loads(_handle_tool_call(repo, "code_graph", {"command": "doctor"}))
    assert "files" in payload
    assert "edges" in payload
    assert "top_hubs" in payload
