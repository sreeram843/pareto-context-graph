"""Tests for detect_changes and architecture_report."""

from __future__ import annotations

import json

from pareto_context_graph.architecture_report import build_architecture_report, write_architecture_report
from pareto_context_graph.graph import build_graph
from pareto_context_graph.graph_diff import detect_changes
from pareto_context_graph.server import _handle_tool_call


def test_detect_changes_empty_diff(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=3)
    store = build_graph(repo, max_commits=40)
    store.close()

    payload = detect_changes(repo, base="main")
    assert "changed" in payload
    assert "stale_index" in payload
    assert isinstance(payload["affected"], list)


def test_architecture_report_writes_markdown(synthetic_repo_factory, tmp_path):
    repo = synthetic_repo_factory(commits=30, files=6, seed=9)
    store = build_graph(repo, max_commits=30)
    store.close()

    text = build_architecture_report(repo)
    assert "# Architecture report" in text
    assert "Top hubs" in text

    out = write_architecture_report(repo, tmp_path / "ARCHITECTURE_REPORT.md")
    assert out.exists()
    assert out.read_text().startswith("# Architecture report")


def test_mcp_detect_changes_and_architecture_report_commands(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=35, files=7, seed=2)
    store = build_graph(repo, max_commits=35)
    store.close()

    dc = json.loads(
        _handle_tool_call(repo, "pareto_context_graph", {"command": "detect_changes", "base": "main"})
    )
    assert "blast_count" in dc

    ar = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {"command": "architecture_report", "write": False},
        )
    )
    assert "report" in ar
    assert "Graph summary" in ar["report"]
