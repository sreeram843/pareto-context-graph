from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test-bot"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_symbol_search_finds_defining_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    target = repo / "pkg" / "auth.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "class OAuth2PasswordBearer:\n"
        "    def __init__(self, tokenUrl: str):\n"
        "        self.tokenUrl = tokenUrl\n",
        encoding="utf-8",
    )
    helper = repo / "pkg" / "other.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Co-change both files so they appear in the graph.
    target.write_text(target.read_text() + "\n# change\n", encoding="utf-8")
    helper.write_text(helper.read_text() + "\n# change\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "pair"], cwd=repo, check=True, capture_output=True)

    build_graph(repo, max_commits=50)

    out = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {"command": "search", "query": "OAuth2PasswordBearer", "limit": 10},
    )
    payload = json.loads(out)
    assert "pkg/auth.py" in payload["files"]
    assert any(hit["symbol"] == "OAuth2PasswordBearer" for hit in payload.get("symbols", []))


def test_query_first_context_default_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PCG_FEATURE_QUERY_FIRST", raising=False)
    repo = tmp_path / "repo2"
    repo.mkdir()
    _init_repo(repo)
    (repo / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (repo / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    (repo / "main.py").write_text((repo / "main.py").read_text() + "\n# change\n", encoding="utf-8")
    (repo / "util.py").write_text((repo / "util.py").read_text() + "\n# change\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "pair"], cwd=repo, check=True, capture_output=True)

    build_graph(repo, max_commits=10)

    allowed = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {"command": "context", "query": "hello function", "tier": 1, "token_budget": 2000},
        )
    )
    assert "error" not in allowed
    assert allowed.get("query_first") is True
    assert allowed["files_included"] >= 1

    store = Store(repo)
    assert store.has_search_index()
    store.close()


def test_query_first_context_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCG_FEATURE_QUERY_FIRST", "0")
    repo = tmp_path / "repo3"
    repo.mkdir()
    _init_repo(repo)
    (repo / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    build_graph(repo, max_commits=10)

    blocked = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {"command": "context", "query": "hello function", "tier": 1, "token_budget": 2000},
        )
    )
    assert "error" in blocked
