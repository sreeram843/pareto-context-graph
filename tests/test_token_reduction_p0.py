"""P0 adaptive stage-1 cap and session memory."""

from __future__ import annotations

import json

from pareto_context_graph.adaptive_cap import (
    LONG_QUERY_CAP,
    SHORT_QUERY_CAP,
    adaptive_stage1_cap,
)
from pareto_context_graph.deadlines import MEGA_HUB_STAGE1_CAP
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.session import (
    clear_session,
    load_session_paths,
    merge_session_already_have,
    record_session_paths,
    session_file,
)
from pareto_context_graph.store import Store


def test_adaptive_cap_short_query():
    assert adaptive_stage1_cap("fix bug", profile_cap=500) == SHORT_QUERY_CAP


def test_adaptive_cap_long_query():
    q = "register api server handlers reconcile controller loop pod lifecycle"
    assert adaptive_stage1_cap(q, profile_cap=500) == LONG_QUERY_CAP


def test_adaptive_cap_empty_query():
    assert adaptive_stage1_cap("", profile_cap=500) == SHORT_QUERY_CAP


def test_adaptive_cap_high_fanout():
    assert adaptive_stage1_cap("anything", profile_cap=800, high_fanout=True) == MEGA_HUB_STAGE1_CAP


def test_adaptive_cap_respects_profile_cap():
    assert adaptive_stage1_cap("x", profile_cap=10) == 10


def test_session_merge_and_record(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    record_session_paths(repo, ["a.py", "b.py"])
    assert load_session_paths(repo) == ["a.py", "b.py"]

    merged, added = merge_session_already_have(repo, {"a.py"}, {"session_memory": True})
    assert added == 1
    assert merged == {"a.py", "b.py"}

    record_session_paths(repo, ["c.py", "a.py"], max_paths=2)
    assert load_session_paths(repo) == ["b.py", "c.py"]

    clear_session(repo)
    assert not session_file(repo).exists()


def test_context_session_auto_fills_already_have(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    seed = "app/main.py"
    for i in range(3):
        store.record_co_change(seed, f"lib/part_{i}.py", weight=5.0)
    store.rebuild_top_neighbours(k=10)
    store.commit()
    store.close()

    record_session_paths(repo, ["lib/part_0.py"])

    raw1 = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": [seed],
            "query": "update handler",
            "tier": 1,
            "token_budget": 5000,
            "session_memory": True,
        },
    )
    first = json.loads(raw1)
    assert "error" not in first
    assert first.get("session_already_have") == 1
    returned = {e["path"] for e in first["context_files"]}
    assert "lib/part_0.py" not in returned

    raw2 = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": [seed],
            "query": "update handler",
            "tier": 1,
            "token_budget": 5000,
            "session_memory": True,
        },
    )
    second = json.loads(raw2)
    assert second.get("skipped_already_have", 0) >= 1
    assert "stage1_cap" in second


def test_context_reports_adaptive_stage1_cap(tmp_path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    store = Store(repo)
    store.record_co_change("a.py", "b.py", weight=3.0)
    store.commit()
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": ["a.py"],
            "query": "x",
            "tier": 1,
            "token_budget": 5000,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    assert payload.get("stage1_cap") == SHORT_QUERY_CAP
