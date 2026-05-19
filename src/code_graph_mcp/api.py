"""Public Python API for code-graph-mcp — no MCP protocol required.

Use this to integrate code-graph context ranking directly into Python scripts
or tools without running an MCP server.

Example usage::

    from pathlib import Path
    from code_graph_mcp.api import CodeGraph

    cg = CodeGraph("/path/to/your/repo")

    # Build the graph (only needed once, or after many new commits)
    cg.build()

    # Get ranked context files for a query
    result = cg.context(
        files=["app/controllers/login_controller.rb"],
        query="add rate limiting to login",
        tier=1,          # 1=summaries, 2=signatures, 3=code chunks
        token_budget=20000,
    )
    for f in result["context_files"]:
        print(f["path"], "-", f.get("summary", ""))

    # Incremental update after new commits (fast, <2s)
    cg.update()

    # Blast radius of current uncommitted diff
    blast = cg.blast()
    print(blast)

    # Direct neighbours of a single file
    neighbours = cg.neighbours("app/services/auth_service.rb")
    print(neighbours)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .server import _handle_tool_call


class CodeGraph:
    """Thin Python wrapper around the code_graph tool.

    Parameters
    ----------
    repo_root:
        Absolute path to the git repository root.
    """

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call(self, command: str, **kwargs: Any) -> dict:
        """Dispatch a command and return the parsed JSON result."""
        arguments = {"command": command, **kwargs}
        raw = _handle_tool_call(self.repo_root, "code_graph", arguments)
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Primary commands
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        max_commits: int = 5000,
        since: str | None = None,
        profile: str | None = None,
        shards: int = 1,
    ) -> dict:
        """Build (or rebuild) the co-change graph from git history.

        Parameters
        ----------
        max_commits:
            Maximum number of commits to analyse (default: 5000).
        since:
            git ``--since`` expression, e.g. ``"12 months ago"`` or
            ``"2025-01-01"``.  Overrides ``max_commits`` window.
        profile:
            Tuning preset: ``tiny``, ``medium``, ``large``, ``huge``.
            Auto-detected when omitted.
        shards:
            Number of parallel shard workers for large repos.
        """
        kwargs: dict[str, Any] = {"max_commits": max_commits, "shards": shards}
        if since is not None:
            kwargs["since"] = since
        if profile is not None:
            kwargs["profile"] = profile
        return self._call("build", **kwargs)

    def update(self) -> dict:
        """Incremental graph update using commits since the last build (<2s)."""
        return self._call("update")

    def context(
        self,
        files: list[str],
        *,
        query: str = "",
        tier: int = 1,
        token_budget: int = 50000,
        already_have: list[str] | None = None,
        min_weight: int | None = None,
        max_depth: int | None = None,
        profile: str | None = None,
    ) -> dict:
        """Return ranked context files for the given seed files and query.

        Parameters
        ----------
        files:
            Files the user is working in or asking about (seed files).
        query:
            The user's question/task — used for TF-IDF keyword ranking.
        tier:
            Detail level:
            - ``1`` (default) — path + one-line summary (~30 tokens/file)
            - ``2`` — class/function signatures (~50–200 tokens/file)
            - ``3`` — relevant code chunks (full content)
        token_budget:
            Maximum tokens to return across all files (default: 50000).
        already_have:
            Files already in context — skipped in the response to avoid
            redundancy across multi-turn conversations.
        min_weight:
            Minimum co-change count to consider an edge (default: 2).
        max_depth:
            BFS hops through the graph (default: 2).
        profile:
            Tuning preset override (``tiny``, ``medium``, ``large``, ``huge``).
        """
        kwargs: dict[str, Any] = {
            "files": files,
            "query": query,
            "tier": tier,
            "token_budget": token_budget,
            "already_have": already_have or [],
        }
        if min_weight is not None:
            kwargs["min_weight"] = min_weight
        if max_depth is not None:
            kwargs["max_depth"] = max_depth
        if profile is not None:
            kwargs["profile"] = profile
        return self._call("context", **kwargs)

    def blast(self, *, base: str = "main") -> dict:
        """Return files affected by the current uncommitted git diff.

        Parameters
        ----------
        base:
            Base branch to diff against (default: ``main``).
        """
        return self._call("blast", base=base)

    def savings(self, *, base: str = "main") -> dict:
        """Compare full-repo token cost vs blast-radius cost for the current diff."""
        return self._call("savings", base=base)

    def neighbours(self, path: str, *, min_weight: int = 1) -> dict:
        """Return direct co-change neighbours for a single file.

        Parameters
        ----------
        path:
            Repo-relative file path.
        min_weight:
            Minimum co-change count threshold.
        """
        return self._call("neighbours", path=path, min_weight=min_weight)

    def stats(self) -> dict:
        """Return file count, edge count, and build metadata."""
        return self._call("stats")

    def doctor(self) -> dict:
        """Graph health diagnostics: hub stats, staleness, build info."""
        return self._call("doctor")

    def hotspots(self, *, top_n: int = 10) -> dict:
        """Return the most-coupled files (architectural hubs).

        Parameters
        ----------
        top_n:
            Number of hotspot files to return (default: 10).
        """
        return self._call("hotspots", top_n=top_n)

    def search(self, query: str, *, limit: int = 20) -> dict:
        """Full-text search over file paths in the graph.

        Parameters
        ----------
        query:
            Search term (FTS5 match expression).
        limit:
            Maximum results (default: 20).
        """
        return self._call("search", query=query, limit=limit)

    def communities(self) -> dict:
        """Detect implicit module clusters from the co-change graph."""
        return self._call("communities")

    def decay_sweep(
        self,
        *,
        half_life_days: float | None = None,
        prune_below: float | None = None,
    ) -> dict:
        """Apply recency decay and prune weak edges.

        Parameters
        ----------
        half_life_days:
            Exponential decay half-life in days.
        prune_below:
            Delete edges whose weight falls below this threshold after decay.
        """
        kwargs: dict[str, Any] = {}
        if half_life_days is not None:
            kwargs["half_life_days"] = half_life_days
        if prune_below is not None:
            kwargs["prune_below"] = prune_below
        return self._call("decay_sweep", **kwargs)

    def mark_used(self, paths: list[str]) -> dict:
        """Record which context files the assistant/user actually used.

        Feedback is persisted to ``weights.json`` for learned ranking boosts.

        Parameters
        ----------
        paths:
            Repo-relative paths of files that were genuinely useful.
        """
        return self._call("mark_used", paths=paths)
