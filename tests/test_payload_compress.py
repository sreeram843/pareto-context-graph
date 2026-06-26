"""Tests for in-house payload compression (Phase A)."""

from __future__ import annotations

import json
from pathlib import Path

from pareto_context_graph.compress_stack import build_compress_stack_block
from pareto_context_graph.graph import build_graph
from pareto_context_graph.payload_compress import (
    COMPRESSION_METHOD,
    apply_context_compression,
    estimate_compressed_tokens,
    prune_body,
    retrieve_payload,
    serialize_context_files,
    store_payload,
)
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.tokenizer import BytesPerTokenTokenizer


def test_prune_body_keeps_query_lines():
    body = "\n".join(
        [
            "def authenticate(user, password):",
            "    token = issue_token(user)",
            "    cache.set(user.id, token)",
            "    return token",
            "def unrelated():",
            "    return 1",
        ]
    )
    pruned = prune_body(body, "authenticate token")
    assert "authenticate" in pruned
    assert "issue_token" in pruned
    assert "unrelated" not in pruned or "# ... pruned" in pruned


def test_store_and_retrieve_payload(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = {"context_files": [{"path": "a.py", "content": "x = 1"}], "tier": 3}
    digest = store_payload(repo, payload)
    loaded = retrieve_payload(repo, digest)
    assert loaded == payload
    assert retrieve_payload(repo, "not-a-hash") is None


def test_apply_context_compression_reduces_tokens():
    tokenizer = BytesPerTokenTokenizer()
    response = {
        "context_files": [
            {
                "path": "auth.py",
                "content": "\n".join(
                    [
                        "def login(u, p):",
                        "    validate(u, p)",
                        "    session.create(u)",
                        "    audit.log('login', u)",
                        "    return session.token",
                    ]
                    * 20
                ),
            }
        ],
        "tokens_used": 500,
        "tier": 3,
    }
    out = apply_context_compression(
        response,
        repo_root=Path("/tmp/unused"),
        query="login session",
        compression="prune",
        tokenizer=tokenizer,
    )
    assert out["compression"] == "prune"
    assert out["compression_method"] == COMPRESSION_METHOD
    assert out["content_hash"]
    assert out["tokens_used"] < out["tokens_before_compress"]
    assert out["retrieve_command"] == "retrieve"


def test_context_prune_and_retrieve_roundtrip(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=20, files=5, seed=9)
    fat_body = "\n".join(
        [
            "def handle_request(req):",
            "    user = auth.verify(req)",
            "    data = db.fetch(user)",
            "    return render(data)",
        ]
        + [f"    _step_{i} = compute_{i}(user)" for i in range(40)]
        + ["", "def debug_only():", "    print('noise')"]
    )
    (repo / "src" / "b.py").write_text(fat_body)

    store = build_graph(repo, max_commits=50)
    store.close()

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": ["src/a.py"],
            "query": "handle_request auth verify",
            "tier": 3,
            "token_budget": 20_000,
            "compression": "prune",
            "tokenizer": "legacy",
        },
    )
    payload = json.loads(raw)
    assert "error" not in payload
    assert payload.get("context_files"), payload
    assert payload.get("content_hash"), "expected prune to shrink tier-3 payload"
    assert payload["tokens_used"] < payload["tokens_before_compress"]

    raw_retrieve = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {"command": "retrieve", "content_hash": payload["content_hash"]},
    )
    restored = json.loads(raw_retrieve)
    assert restored["payload"]["context_files"]
    pruned_len = len(json.dumps(payload["context_files"]))
    restored_len = len(json.dumps(restored["payload"]["context_files"]))
    assert restored_len >= pruned_len


def test_estimate_compressed_tokens_for_eval():
    files = [{"path": "a.py", "content": "def foo():\n" + "    pass\n" * 80}]
    text = serialize_context_files(files)
    result = estimate_compressed_tokens(text, query="foo")
    assert result["method"] == COMPRESSION_METHOD
    assert result["tokens_after"] < result["tokens_before"]


def test_build_compress_stack_block_estimate_path():
    response = {
        "tokens_used": 1000,
        "context_files": [{"path": "a.py", "content": "print('hi')\n" * 50}],
        "context_savings": {"naive_corpus_tokens": 50000},
    }
    block = build_compress_stack_block(response, query="print")
    assert block["graph_tokens"] == 1000
    assert block["compressed_tokens"] < 1000
    assert block["retrieve_command"] == "retrieve"
    assert block["compression_method"] == COMPRESSION_METHOD
