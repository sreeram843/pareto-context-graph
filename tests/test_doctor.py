from __future__ import annotations

import json

from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call


def test_doctor_command_returns_expected_keys(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=5)
    store = build_graph(repo, max_commits=100)
    store.close()

    payload = json.loads(_handle_tool_call(repo, "pareto_context_graph", {"command": "doctor"}))
    assert "files" in payload
    assert "edges" in payload
    assert "top_hubs" in payload
    assert "build_estimate" in payload
