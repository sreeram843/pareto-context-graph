"""Week 6: treesitter default, route edges, ops at scale."""

from __future__ import annotations

import json

import pytest

from pareto_context_graph.daemon import GraphWatcher
from pareto_context_graph.repo_registry import RepoRegistry, build_repo_registry
from pareto_context_graph.route_edges import extract_route_edges
from pareto_context_graph.structural import extract_structural_edges
from pareto_context_graph.symbols import symbol_index_mode, treesitter_installed, use_treesitter_for_symbols
from pareto_context_graph.watcher_health import mark_error, reset_for_tests, snapshot


def test_treesitter_default_on_when_installed(monkeypatch):
    monkeypatch.delenv("PCG_FEATURE_TREESITTER", raising=False)
    if not treesitter_installed():
        pytest.skip("tree-sitter extras not installed")
    assert use_treesitter_for_symbols() is True
    assert symbol_index_mode() == "treesitter"


def test_treesitter_opt_out(monkeypatch):
    monkeypatch.setenv("PCG_FEATURE_TREESITTER", "0")
    assert use_treesitter_for_symbols() is False
    assert symbol_index_mode() == "regex"


def test_route_edges_from_fastapi_style_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    routes = repo / "routes.py"
    handlers = repo / "handlers.py"
    routes.write_text(
        "from fastapi import APIRouter\n"
        "from handlers import get_item\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return get_item()\n"
    )
    handlers.write_text("def get_item():\n    return {}\n")
    all_files = {"routes.py", "handlers.py"}
    edges = extract_route_edges(routes, "routes.py", all_files)
    dst = {edge["dst_path"] for edge in edges}
    assert "handlers.py" in dst
    assert all(edge["kind"] == "route" for edge in edges)


def test_structural_includes_route_edges(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    routes = repo / "routes.py"
    handlers = repo / "handlers.py"
    routes.write_text(
        "from fastapi import APIRouter\n"
        "from handlers import get_item\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return get_item()\n"
    )
    handlers.write_text("def get_item():\n    return {}\n")
    all_files = {"routes.py", "handlers.py"}
    edges = extract_structural_edges(routes, "routes.py", all_files)
    kinds = {edge["kind"] for edge in edges}
    assert "route" in kinds


def test_repo_registry_resolve():
    reg = RepoRegistry({"default": "/a", "svc": "/b"})
    assert reg.resolve() == reg.resolve("default")
    assert reg.resolve("svc").as_posix().endswith("/b")
    with pytest.raises(KeyError):
        reg.resolve("missing")


def test_build_repo_registry_primary_and_map(tmp_path):
    primary = tmp_path / "main"
    secondary = tmp_path / "svc"
    primary.mkdir()
    secondary.mkdir()
    reg = build_repo_registry(primary, [f"svc={secondary}"])
    assert reg.resolve().resolve() == primary.resolve()
    assert reg.resolve("svc").resolve() == secondary.resolve()


def test_watcher_health_records_errors():
    reset_for_tests()
    mark_error("sync failed")
    snap = snapshot()
    assert snap["error_count"] == 1
    assert "sync failed" in str(snap["last_error"])


def test_mcp_repo_key_routing(synthetic_repo_factory):
    from pareto_context_graph.server import _handle_tool_call

    repo_a = synthetic_repo_factory(commits=30, files=6, seed=81)
    repo_b = synthetic_repo_factory(commits=30, files=6, seed=82)
    from pareto_context_graph.graph import build_graph_sharded

    build_graph_sharded(repo_a, max_commits=40, shards=1, profile_name="tiny").close()
    build_graph_sharded(repo_b, max_commits=40, shards=1, profile_name="tiny").close()

    reg = RepoRegistry({"default": repo_a, "other": repo_b})
    raw = _handle_tool_call(
        reg.resolve("other"),
        "pareto_context_graph",
        {"command": "stats"},
    )
    payload = json.loads(raw)
    assert payload.get("files", 0) > 0


def test_doctor_symbol_index_warning(synthetic_repo_factory, monkeypatch):
    from pareto_context_graph.build_estimate import gather_doctor_report
    from pareto_context_graph.doctor import format_doctor_text

    monkeypatch.setenv("PCG_FEATURE_TREESITTER", "0")
    repo = synthetic_repo_factory(commits=30, files=6, seed=83)
    from pareto_context_graph.graph import build_graph_sharded

    build_graph_sharded(repo, max_commits=40, shards=1, profile_name="tiny").close()
    report = gather_doctor_report(repo)
    text = format_doctor_text(report)
    assert report["symbol_index"]["mode"] == "regex"
    assert "regex" in text.lower() or "approximate" in text.lower()
