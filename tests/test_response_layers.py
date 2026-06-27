"""Phase 15.8 dual-layer response tests."""

from __future__ import annotations

import json

from pareto_context_graph.graph import build_graph
from pareto_context_graph.response_layers import apply_dual_layer_response
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.spec_index import rebuild_spec_indexes
from pareto_context_graph.store import Store


def test_apply_dual_layer_response_shapes_spec():
    shaped = apply_dual_layer_response(
        {
            "response_version": 2,
            "context_files": [{"path": "src/a.py"}],
            "tier": 1,
            "tokens_used": 42,
            "files_included": 1,
            "files_available": 3,
            "spec_context": [{"path": "docs/a.md", "snippet": "hello"}],
        }
    )
    assert shaped["response_version"] == 3
    assert shaped["code_context"]["context_files"][0]["path"] == "src/a.py"
    assert shaped["spec_context"]["count"] == 1
    assert shaped["spec_context"]["snippets"][0]["path"] == "docs/a.md"
    assert shaped["context_files"][0]["path"] == "src/a.py"


def test_apply_dual_layer_response_null_spec():
    shaped = apply_dual_layer_response(
        {
            "context_files": [],
            "tier": 1,
            "tokens_used": 0,
            "files_included": 0,
            "files_available": 0,
        }
    )
    assert shaped["spec_context"] is None
    assert shaped["code_context"]["files_included"] == 0


def test_context_response_version_3(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=5)
    store = build_graph(repo, max_commits=60)
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": ["src/a.py"],
            "query": "helper",
            "tier": 1,
            "token_budget": 8000,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    assert payload["response_version"] == 3
    assert payload["code_context"]["context_files"]
    assert payload["spec_context"] is None


def test_context_dual_layer_with_specs(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=6)
    store = build_graph(repo, max_commits=60)
    store.close()

    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "feature.md").write_text(
        "# Feature X\n\nScheduler subsystem coordinates cron jobs.\n"
    )
    store = Store(repo)
    try:
        rebuild_spec_indexes(store, repo)
        store.commit()
    finally:
        store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": ["src/a.py"],
            "query": "scheduler cron subsystem",
            "tier": 1,
            "token_budget": 8000,
            "include_specs": True,
            "spec_limit": 3,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    assert payload["response_version"] == 3
    spec = payload["spec_context"]
    assert spec is not None
    assert spec["count"] >= 1
    assert any("feature.md" in str(item.get("path", "")) for item in spec["snippets"])
