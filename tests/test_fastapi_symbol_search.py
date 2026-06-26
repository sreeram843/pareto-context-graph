"""Phase 11.1 acceptance: symbol search on the fastapi bench graph."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store

FASTAPI_BENCH = Path(__file__).resolve().parents[1] / "bench" / "fastapi"


@pytest.fixture(scope="module")
def fastapi_repo() -> Path:
    if not (FASTAPI_BENCH / ".git").is_dir():
        pytest.skip("bench/fastapi not cloned (run: make bench-setup-t1)")
    store = Store(FASTAPI_BENCH)
    try:
        if not store.has_search_index():
            pytest.skip("fastapi search index missing (run: pareto-context-graph build on bench/fastapi)")
    finally:
        store.close()
    return FASTAPI_BENCH


def test_fastapi_search_finds_oauth2_symbol(fastapi_repo: Path):
    payload = json.loads(
        _handle_tool_call(
            fastapi_repo,
            "pareto_context_graph",
            {"command": "search", "query": "OAuth2PasswordBearer", "limit": 10},
        )
    )
    assert "fastapi/security/oauth2.py" in payload.get("files", [])
    symbols = payload.get("symbols") or []
    assert any(hit.get("symbol") == "OAuth2PasswordBearer" for hit in symbols)


def test_fastapi_query_first_context_surfaces_oauth2_file(fastapi_repo: Path):
    payload = json.loads(
        _handle_tool_call(
            fastapi_repo,
            "pareto_context_graph",
            {
                "command": "context",
                "query": "OAuth2PasswordBearer password bearer token authentication",
                "tier": 1,
                "token_budget": 6000,
                "session_memory": False,
            },
        )
    )
    assert "error" not in payload
    paths = [entry["path"] for entry in payload.get("context_files", [])]
    assert "fastapi/security/oauth2.py" in paths[:10]
