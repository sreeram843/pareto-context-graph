"""Context savings reporting for graph vs naive baselines."""

from __future__ import annotations

from pathlib import Path

from .tokens import estimate_file_tokens, repo_tracked_count


def estimate_paths_tokens(repo_root: Path, paths: list[str]) -> int:
    total = 0
    for path in paths:
        fp = repo_root / path
        if fp.is_file():
            total += estimate_file_tokens(fp)
    return total


def corpus_token_estimate(repo_root: Path) -> int:
    import subprocess

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    paths = [p for p in result.stdout.strip().splitlines() if p]
    return estimate_paths_tokens(repo_root, paths)


def agent_grep_token_estimate(repo_root: Path, query: str) -> int:
    from .eval import agent_grep_top_files

    paths = agent_grep_top_files(repo_root, query)
    if not paths:
        return 0
    return estimate_paths_tokens(repo_root, paths)


def token_reduction_row(
    repo_root: Path,
    *,
    graph_tokens: int,
    query: str = "",
    seed_files: list[str] | None = None,
) -> dict:
    """Agent baseline vs graph tokens for bench/eval rows."""
    agent_tokens = agent_grep_token_estimate(repo_root, query or " ".join(seed_files or []))
    reduction_vs_agent = (
        round(agent_tokens / graph_tokens, 2) if graph_tokens and agent_tokens else 0.0
    )
    return {
        "graph_tokens": graph_tokens,
        "agent_baseline_tokens": agent_tokens,
        "reduction_vs_agent": reduction_vs_agent,
    }


def build_context_savings(
    repo_root: Path,
    *,
    graph_tokens: int,
    tokenizer: str,
    query: str = "",
    seed_files: list[str] | None = None,
) -> dict:
    """Build the context_savings block for a context response."""
    corpus_tokens = corpus_token_estimate(repo_root)
    agent_tokens = agent_grep_token_estimate(repo_root, query or " ".join(seed_files or []))

    reduction_vs_corpus = round(corpus_tokens / graph_tokens, 1) if graph_tokens else 0.0
    reduction_vs_agent = (
        round(agent_tokens / graph_tokens, 1) if graph_tokens and agent_tokens else 0.0
    )

    method = "tiktoken" if tokenizer.startswith("tiktoken:") else "estimated"
    return {
        "naive_corpus_tokens": corpus_tokens,
        "agent_baseline_tokens": agent_tokens,
        "graph_tokens": graph_tokens,
        "reduction_ratio": reduction_vs_corpus,
        "reduction_vs_agent": reduction_vs_agent,
        "corpus_files": repo_tracked_count(repo_root),
        "method": method,
        "tokenizer": tokenizer,
    }
