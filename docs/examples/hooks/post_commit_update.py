"""Post-commit hook preset: nudge graph update when index is stale (GitNexus pattern).

After git commit/merge, run doctor or detect_changes; if stale_index, remind to:

    pareto-context-graph update

Example Cursor post-tool hook checks MCP response for stale_index from detect_changes.
"""

from __future__ import annotations


def post_commit_hint(changed_files: list[str] | None = None) -> dict:
    return {
        "action": "pareto_context_graph update",
        "reason": "post_commit_stale_index",
        "changed_files": changed_files or [],
        "hint": "Run update (or build) so co-change edges reflect the latest history.",
    }
