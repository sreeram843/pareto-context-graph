from __future__ import annotations

from pathlib import Path

from pareto_context_graph.savings import build_context_savings
from pareto_context_graph.structural import extract_structural_edges, infer_test_target, is_test_path


def test_is_test_path():
    assert is_test_path("tests/test_auth.py")
    assert not is_test_path("src/auth.py")


def test_infer_test_target():
    files = {"src/auth.py", "tests/test_auth.py"}
    assert infer_test_target("tests/test_auth.py", files) == "src/auth.py"


def test_extract_structural_import_edge(tmp_path: Path):
    repo = tmp_path
    consumer = repo / "consumer.py"
    provider = repo / "provider.py"
    provider.write_text("VALUE = 1\n", encoding="utf-8")
    consumer.write_text("from provider import VALUE\n", encoding="utf-8")
    all_files = {"consumer.py", "provider.py"}
    edges = extract_structural_edges(consumer, "consumer.py", all_files)
    kinds = {(e["dst_path"], e["kind"]) for e in edges}
    assert ("provider.py", "calls") in kinds


def test_build_context_savings_monotonic(tmp_path: Path):
    panel = build_context_savings(
        tmp_path,
        graph_tokens=100,
        tokenizer="legacy",
        query="hello",
    )
    assert panel["graph_tokens"] == 100
    assert panel["naive_corpus_tokens"] >= 0
    assert panel["tokenizer"] == "legacy"
    assert panel["method"] == "estimated"
