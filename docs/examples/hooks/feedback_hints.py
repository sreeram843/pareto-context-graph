"""Example post_context hook — remind agents to log feedback after context.

Copy into your repo:

    mkdir -p .pareto-context-graph/hooks
    cp docs/examples/hooks/feedback_hints.py .pareto-context-graph/hooks/

Or from the project root after cloning pareto-context-graph:

    cp path/to/pareto-context-graph/docs/examples/hooks/feedback_hints.py .pareto-context-graph/hooks/

See docs/HOOKS.md and docs/FEEDBACK.md.
"""

from __future__ import annotations

_MAX_HINT_PATHS = 5


def post_context(response: dict) -> dict:
    """Attach copy-paste MCP feedback commands using this response's request_id."""
    request_id = response.get("request_id")
    if not request_id:
        return response

    paths = [
        str(entry.get("path", ""))
        for entry in response.get("context_files", [])
        if entry.get("path")
    ]
    if not paths:
        return response

    sample = paths[:_MAX_HINT_PATHS]
    primary = sample[0]

    response["feedback_hints"] = {
        "request_id": request_id,
        "paths_in_response": sample,
        "commands": {
            "accept_helpful": {
                "command": "feedback_accept",
                "request_id": request_id,
                "paths": [primary],
            },
            "mark_used_in_edit": {
                "command": "mark_used",
                "request_id": request_id,
                "paths": [primary],
            },
            "reject_noise": {
                "command": "feedback_reject",
                "request_id": request_id,
                "paths": [primary],
            },
            "dwell_30s_plus": {
                "command": "feedback_dwell",
                "request_id": request_id,
                "paths": [primary],
                "dwell_seconds": 30,
            },
        },
        "agent_note": (
            "Replace paths with files the user actually used. "
            "Strongest signal: mark_used. Schedule: pareto-context-graph learn"
        ),
    }
    return response
