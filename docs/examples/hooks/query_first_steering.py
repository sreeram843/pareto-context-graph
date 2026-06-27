"""Cursor hook preset: prefer PCG context before broad file reads (Graphify/GitNexus pattern).

Wire in `.cursor/hooks.json` as a beforeSubmit or pre-tool hook that injects:

    Before searching or reading multiple source files, call pareto_context_graph
    with command=context, tier=1, query_first=true, session_memory=false.
    Use the returned paths instead of repo-wide grep when ranking is available.

See docs/COMMANDS.md for tier escalation via suggested_next.
"""

from __future__ import annotations


def steering_hint(payload: dict) -> dict:
    """Return a short reminder for hook runners that support payload mutation."""
    payload.setdefault("pcg_steering", {})
    payload["pcg_steering"] = {
        "prefer": "pareto_context_graph context tier=1 query_first",
        "avoid": "repo-wide grep before context",
        "session_memory": False,
    }
    return payload
