from __future__ import annotations

from pareto_context_graph.embed import build_embeddings, load_embedding_index, query_embedding_scores


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
