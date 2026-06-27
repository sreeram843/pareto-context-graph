"""Week 4: snapshot onboarding, install v2, affected test selection."""

from __future__ import annotations

import json
import subprocess

from pareto_context_graph.agent_install import (
    PCG_MARKER_END,
    PCG_MARKER_START,
    install_agent,
    print_agent_config,
    uninstall_agent,
)
from pareto_context_graph.affected import compute_affected_tests, matches_test_glob
from pareto_context_graph.graph import build_graph_sharded
from pareto_context_graph.store import Store


def test_matches_test_glob():
    assert matches_test_glob("tests/test_foo.py", ["test_*.py"])
    assert matches_test_glob("pkg/foo_test.go", ["*_test.go"])
    assert not matches_test_glob("src/foo.py", ["test_*.py"])


def test_install_writes_markers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    messages = install_agent(repo, "cursor", location="local", force=True)
    assert any("Cursor MCP" in m for m in messages)
    agents = repo / "AGENTS.md"
    assert agents.exists()
    text = agents.read_text()
    assert PCG_MARKER_START in text
    assert PCG_MARKER_END in text
    assert (repo / ".cursor" / "mcp.json").exists()


def test_uninstall_removes_markers(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    install_agent(repo, "cursor", force=True)
    messages = uninstall_agent(repo, "cursor")
    assert messages
    if (repo / "AGENTS.md").exists():
        assert PCG_MARKER_START not in (repo / "AGENTS.md").read_text()


def test_print_config_cursor(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = print_agent_config(repo, "cursor")
    assert "mcpServers" in payload
    assert "pareto-context-graph" in payload["mcpServers"]


def test_affected_reverse_structural(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=8, seed=71)
    store = build_graph_sharded(
        repo,
        max_commits=70,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    test_path = "tests/test_f0.py"
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_f0.py").write_text("from src.f0 import helper\n")
    subprocess.run(["git", "add", "tests/test_f0.py"], cwd=repo, check=True)
    store.add_structural_edge(test_path, "src/f0.py", "calls")
    store.commit()
    store.close()

    store = Store(repo)
    try:
        payload = compute_affected_tests(store, repo, ["src/f0.py"], max_depth=2)
    finally:
        store.close()

    assert "tests/test_f0.py" in payload["tests"]


def test_affected_mcp_handler(synthetic_repo_factory):
    from pareto_context_graph.server import _handle_tool_call

    repo = synthetic_repo_factory(commits=50, files=8, seed=72)
    store = build_graph_sharded(
        repo,
        max_commits=70,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_f0.py").write_text("# test\n")
    subprocess.run(["git", "add", "tests/test_f0.py"], cwd=repo, check=True)
    store.add_structural_edge("tests/test_f0.py", "src/f0.py", "calls")
    store.commit()
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {"command": "affected", "paths": ["src/f0.py"]},
    )
    payload = json.loads(raw)
    assert payload["test_count"] >= 1
