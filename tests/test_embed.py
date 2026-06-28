from __future__ import annotations

import pytest

from pareto_context_graph.embed import (
    DeterministicNoopBackend,
    LocalBackend,
    OllamaBackend,
    OpenAIBackend,
    build_embeddings,
    load_embedding_index,
    query_embedding_scores,
    select_embeddings_backend,
)


def test_select_embeddings_backend_by_name():
    assert isinstance(select_embeddings_backend("noop"), DeterministicNoopBackend)
    assert isinstance(select_embeddings_backend(""), DeterministicNoopBackend)
    assert isinstance(select_embeddings_backend("openai"), OpenAIBackend)
    assert isinstance(select_embeddings_backend("ollama"), OllamaBackend)
    assert isinstance(select_embeddings_backend("local"), LocalBackend)
    with pytest.raises(ValueError):
        select_embeddings_backend("bogus")


def test_local_backend_errors_without_fastembed():
    # The optional dependency is not bundled; encoding must raise a helpful error
    # (skip if fastembed happens to be installed in the env).
    pytest.importorskip  # keep import-time light
    try:
        import fastembed  # noqa: F401

        pytest.skip("fastembed installed")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match="fastembed"):
        LocalBackend().encode(["hello"])


def test_build_records_backend_name(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=10, files=6, seed=5)
    build_embeddings(repo)  # default noop
    index = load_embedding_index(repo)
    assert index is not None
    assert index.get("backend") == "noop"


def test_embed_build_creates_index(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=10, files=6, seed=2)
    result = build_embeddings(repo)
    assert result["count"] > 0
    assert result["dims"] > 0

    index = load_embedding_index(repo)
    assert index is not None
    assert index["count"] == result["count"]

    scores = query_embedding_scores(repo, "auth endpoint", ["src/a.py", "src/b.py"])
    assert isinstance(scores, dict)
