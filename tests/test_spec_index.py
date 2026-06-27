"""Phase 15.5 spec/doc hybrid search tests."""

from __future__ import annotations

import json
from pathlib import Path

from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.spec_index import (
    classify_spec_kind,
    discover_spec_files,
    is_spec_path,
    rebuild_spec_indexes,
    search_spec_context,
)
from pareto_context_graph.store import Store


def test_is_spec_path():
    assert is_spec_path("docs/ARCHITECTURE.md")
    assert is_spec_path(".cursor/rules/auth.mdc")
    assert is_spec_path("AGENTS.md")
    assert not is_spec_path("src/main.py")


def test_classify_spec_kind():
    assert classify_spec_kind(".cursor/rules/foo.mdc") == "rule"
    assert classify_spec_kind("AGENTS.md") == "agents"
    assert classify_spec_kind("docs/guide.md") == "doc"


def test_spec_index_and_search(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "auth.md").write_text(
        "# Authentication\n\nOAuth2 bearer tokens and password flows.\n"
    )
    (repo / ".cursor" / "rules").mkdir(parents=True)
    (repo / ".cursor" / "rules" / "api.mdc").write_text(
        "# API rules\n\nAlways validate OpenAPI schema changes.\n"
    )
    (repo / "AGENTS.md").write_text("# Agents\n\nUse tier-1 context first.\n")

    store = Store(repo)
    try:
        stats = rebuild_spec_indexes(store, repo)
        store.commit()
        assert stats["indexed"] >= 3
        hits = search_spec_context(store, repo, "OAuth2 bearer", limit=3)
        assert hits
        assert any("auth.md" in str(h["path"]) for h in hits)
    finally:
        store.close()


def test_context_include_specs(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=4)
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
    assert "error" not in payload
    spec = payload.get("spec_context")
    assert spec is not None
    snippets = spec["snippets"] if isinstance(spec, dict) else spec
    assert any("feature.md" in str(item.get("path", "")) for item in snippets)


def test_search_includes_spec_hits(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=30, files=6, seed=2)
    store = build_graph(repo, max_commits=50)
    store.close()

    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "deploy.md").write_text("# Deploy\n\nKubernetes rollout checklist.\n")
    store = Store(repo)
    try:
        rebuild_spec_indexes(store, repo)
        store.commit()
        payload = store.unified_search("kubernetes rollout", limit=5)
        assert payload.get("spec_hits")
    finally:
        store.close()


def test_discover_spec_files_from_git(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("hello")
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    paths = discover_spec_files(repo)
    assert "docs/a.md" in paths
