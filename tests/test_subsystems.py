"""Phase 15.6 subsystem map tests."""

from __future__ import annotations

import json

from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store
from pareto_context_graph.subsystems import (
    build_subsystem_registry,
    list_subsystems,
    subsystem_files,
)


def test_auto_subsystems_group_src_packages(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=12, seed=7)
    store = build_graph(repo, max_commits=80)
    try:
        registry = build_subsystem_registry(store, repo, min_files=2, max_auto=20)
        assert registry
        assert any(key == "src" or key.startswith("src/") for key in registry)
    finally:
        store.close()


def test_manual_subsystem_from_context_map(synthetic_repo_factory, tmp_path):
    repo = synthetic_repo_factory(commits=40, files=10, seed=3)
    store = build_graph(repo, max_commits=60)
    store.close()

    pcg = repo / ".pareto-context-graph"
    pcg.mkdir(exist_ok=True)
    (pcg / "context-map.json").write_text(
        json.dumps(
            {
                "subsystems": {
                    "core": {
                        "path_globs": ["src/a.py", "src/b.py"],
                        "specs": ["docs/core.md"],
                    }
                }
            }
        )
    )

    store = Store(repo)
    try:
        payload = subsystem_files(store, repo, "core", file_limit=10)
        assert payload["source"] == "manual"
        assert "src/a.py" in payload["files"]
        assert payload["specs"] == ["docs/core.md"]
    finally:
        store.close()


def test_list_subsystems_mcp_command(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=4)
    store = build_graph(repo, max_commits=70)
    store.close()

    raw = _handle_tool_call(repo, "pareto_context_graph", {"command": "list_subsystems"})
    payload = json.loads(raw)
    assert payload["count"] >= 1
    assert payload["subsystems"][0]["key"]


def test_subsystem_files_unknown_key(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=30, files=8, seed=2)
    store = build_graph(repo, max_commits=50)
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {"command": "subsystem_files", "subsystem": "does-not-exist"},
    )
    payload = json.loads(raw)
    assert payload["error"] == "unknown_subsystem"


def test_list_subsystems_orders_by_file_count(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=80, files=20, seed=8)
    store = build_graph(repo, max_commits=100)
    try:
        payload = list_subsystems(store, repo, min_files=2, max_auto=30)
        counts = [item["file_count"] for item in payload["subsystems"]]
        assert counts == sorted(counts, reverse=True)
    finally:
        store.close()
