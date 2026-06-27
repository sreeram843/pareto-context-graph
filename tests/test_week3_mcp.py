"""Week 3: MCP instructions, staleness banners, explore preset, schema trim."""

from __future__ import annotations

import json
import time

from pareto_context_graph.daemon import GraphWatcher
from pareto_context_graph.graph import build_graph_sharded
from pareto_context_graph.indexing import list_pending_index_paths
from pareto_context_graph.server import (
    _attach_staleness,
    _handle_tool_call,
    build_mcp_tools,
)
from pareto_context_graph.server_instructions import (
    SERVER_INSTRUCTIONS,
    SERVER_INSTRUCTIONS_NO_GRAPH,
    build_server_instructions,
    graph_db_exists,
)
from pareto_context_graph.staleness import (
    catch_up_on_connect,
    format_staleness_banner,
    gather_staleness_report,
    reset_catchup_state,
)
from pareto_context_graph.store import Store


def test_server_instructions_without_graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert not graph_db_exists(repo)
    assert build_server_instructions(repo) == SERVER_INSTRUCTIONS_NO_GRAPH


def test_server_instructions_with_graph(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=91)
    store = build_graph_sharded(repo, max_commits=60, shards=1, profile_name="tiny")
    store.close()
    assert graph_db_exists(repo)
    text = build_server_instructions(repo)
    assert text == SERVER_INSTRUCTIONS
    assert "explore" in text
    assert "co-change" in text


def test_mcp_commands_schema_trim(monkeypatch):
    monkeypatch.setenv("PCG_MCP_COMMANDS", "context,explore,search,doctor")
    tools = build_mcp_tools()
    enum = tools[0]["inputSchema"]["properties"]["command"]["enum"]
    assert set(enum) == {"context", "explore", "search", "doctor"}
    assert "build" not in enum


def test_explore_delegates_to_context(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=10, seed=92)
    store = build_graph_sharded(repo, max_commits=80, shards=1, profile_name="tiny")
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {"command": "explore", "query": "co-change", "files": ["src/a.py"]},
    )
    payload = json.loads(raw)
    assert "error" not in payload
    assert "context_files" in payload or "seed_files" in payload


def test_staleness_banner_when_file_edited(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=10, seed=93)
    store = build_graph_sharded(
        repo,
        max_commits=80,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    store.close()

    target = repo / "src" / "f0.py"
    target.write_text(target.read_text() + "\n# stale edit\n")

    store = Store(repo, readonly=True)
    try:
        report = gather_staleness_report(store, repo)
        assert report["pending_count"] >= 1
        banner = format_staleness_banner(report)
        assert banner.startswith("⚠️")
        assert "stale" in banner.lower() or "staleness" in banner.lower()
    finally:
        store.close()


def test_attach_staleness_prepends_banner(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=10, seed=94)
    store = build_graph_sharded(
        repo,
        max_commits=80,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    store.close()

    target = repo / "src" / "f0.py"
    target.write_text(target.read_text() + "\n# banner test\n")

    body = json.dumps({"ok": True})
    out = _attach_staleness(repo, "context", body)
    assert out.startswith("⚠️")
    payload = json.loads(out.split("\n", 1)[1])
    assert payload["ok"] is True
    assert "staleness" in payload


def test_catch_up_on_connect_syncs_pending(synthetic_repo_factory, monkeypatch):
    monkeypatch.setenv("PCG_CATCHUP_ON_CONNECT", "1")
    monkeypatch.setenv("PCG_CATCHUP_MAX_FILES", "20")
    reset_catchup_state()

    repo = synthetic_repo_factory(commits=60, files=10, seed=95)
    store = build_graph_sharded(
        repo,
        max_commits=80,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    store.close()

    target = repo / "src" / "f0.py"
    target.write_text(target.read_text() + "\n# catchup\n")

    result = catch_up_on_connect(repo)
    assert result.get("synced", 0) >= 1

    store = Store(repo, readonly=True)
    try:
        assert list_pending_index_paths(store, repo, limit=5) == []
    finally:
        store.close()


def test_graph_watcher_poll_debounce(synthetic_repo_factory, monkeypatch):
    monkeypatch.setenv("PCG_WATCH_POLL_MS", "50")
    monkeypatch.setenv("PCG_WATCH_DEBOUNCE_MS", "100")

    repo = synthetic_repo_factory(commits=40, files=6, seed=96)
    store = build_graph_sharded(
        repo,
        max_commits=60,
        shards=1,
        profile_name="tiny",
        search_index_mode="eager",
    )
    store.close()

    target = repo / "src" / "f0.py"
    original = target.read_text()
    target.write_text(original + "\n# watcher\n")

    watcher = GraphWatcher(repo, interval=3600, debounce_ms=100)
    synced: list[set[str]] = []

    def _capture(paths: set[str]) -> None:
        synced.append(set(paths))

    watcher._sync_search_index = _capture  # type: ignore[method-assign]
    watcher.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and not synced:
            time.sleep(0.05)
    finally:
        watcher.stop()

    assert synced, "poll watcher should debounce-sync edited files"
