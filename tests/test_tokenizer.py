from __future__ import annotations

import json

import pytest

from pareto_context_graph.tokenizer import (
    BytesPerTokenTokenizer,
    TiktokenTokenizer,
    count_json,
    resolve_tokenizer,
)


def test_bytes_per_token_tokenizer():
    tok = BytesPerTokenTokenizer(bytes_per_token=4)
    assert tok.count("abcd") == 1
    assert tok.count("abcdefgh") == 2
    assert tok.count("") == 0


def test_resolve_tokenizer_legacy():
    tok = resolve_tokenizer("legacy")
    assert tok.name == "legacy"
    assert tok.count("x" * 8) == 2


def test_count_json():
    tok = BytesPerTokenTokenizer(bytes_per_token=4)
    assert count_json(tok, {"path": "a.py", "summary": "test"}) > 0


def test_tiktoken_when_available():
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        pytest.skip("tiktoken not installed")

    tok = resolve_tokenizer("cl100k_base")
    assert tok.name == "tiktoken:cl100k_base"
    text = "def hello_world():\n    return 42\n"
    assert tok.count(text) > 0
    assert TiktokenTokenizer("cl100k_base").count(text) == tok.count(text)


def test_resolve_auto_prefers_tiktoken_when_installed():
    try:
        import tiktoken  # noqa: F401
    except ImportError:
        pytest.skip("tiktoken not installed")

    tok = resolve_tokenizer("auto")
    assert tok.name.startswith("tiktoken:")


def test_resolve_auto_falls_back_without_tiktoken(monkeypatch):
    import pareto_context_graph.tokenizer as tokenizer_mod

    original = tokenizer_mod.TiktokenTokenizer

    class _BrokenTiktoken:
        def __init__(self, *args, **kwargs):
            raise ImportError("no tiktoken")

    monkeypatch.setattr(tokenizer_mod, "TiktokenTokenizer", _BrokenTiktoken)
    tok = resolve_tokenizer("auto")
    assert tok.name == "legacy"


def test_unknown_tokenizer_raises():
    with pytest.raises(ValueError, match="Unknown tokenizer"):
        resolve_tokenizer("not-a-real-encoding")


def test_entry_serialization_is_stable():
    tok = BytesPerTokenTokenizer()
    payload = {"path": "src/a.py", "summary": "module a"}
    a = count_json(tok, payload)
    b = count_json(tok, payload)
    assert a == b
    assert a == tok.count(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
