"""Tests for suggested_next agent hints."""

from __future__ import annotations

import json

from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store
from pareto_context_graph.suggested_next import build_suggested_next


def test_build_suggested_next_tier1():
    hint = build_suggested_next(
        tier=1,
        context_files=[{"path": "a.py"}, {"path": "b.py"}],
        compression="none",
        truncated=False,
    )
    assert hint is not None
    assert hint["tier"] == 2
    assert hint["paths"] == ["a.py", "b.py"]


def test_build_suggested_next_truncated():
    hint = build_suggested_next(
        tier=1,
        context_files=[{"path": "a.py"}],
        compression="none",
        truncated=True,
        timed_out_phase="rank",
    )
    assert hint["reason"] == "truncated_or_timeout"


def test_context_includes_suggested_next_by_default(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    seed = "app/main.py"
    (repo / "app").mkdir(parents=True)
    (repo / seed).write_text("def main():\n    pass\n")
    for i in range(3):
        path = f"lib/part_{i}.py"
        (repo / "lib").mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(f"def part_{i}():\n    pass\n")
    store = Store(repo)
    for i in range(3):
        store.record_co_change(seed, f"lib/part_{i}.py", weight=5.0)
    store.rebuild_top_neighbours(k=10)
    store.commit()
    store.close()

    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": [seed],
                "query": "handler",
                "tier": 1,
                "token_budget": 8000,
            },
        )
    )
    assert "suggested_next" in payload
    assert payload["suggested_next"]["tier"] == 2


def test_session_clear_mcp_command(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    from pareto_context_graph.session import record_session_paths, session_file

    record_session_paths(repo, ["a.py"])
    assert session_file(repo).exists()

    payload = json.loads(
        _handle_tool_call(repo, "pareto_context_graph", {"command": "session_clear"})
    )
    assert payload["cleared"] is True
    assert not session_file(repo).exists()
