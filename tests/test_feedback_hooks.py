"""Feedback hook example (Phase 13.4)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store

EXAMPLE_HOOK = Path(__file__).resolve().parents[1] / "docs/examples/hooks/feedback_hints.py"


def test_feedback_hints_hook_enriches_context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = repo / ".pareto-context-graph" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(EXAMPLE_HOOK, hooks / "feedback_hints.py")

    seed = "app/main.py"
    (repo / "app").mkdir(parents=True)
    (repo / seed).write_text("def main():\n    pass\n")
    (repo / "lib").mkdir(parents=True)
    (repo / "lib/helper.py").write_text("def help():\n    pass\n")

    store = Store(repo)
    store.record_co_change(seed, "lib/helper.py", weight=5.0)
    store.rebuild_top_neighbours(k=10)
    store.commit()
    store.close()

    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": [seed],
                "query": "helper",
                "tier": 1,
                "token_budget": 8000,
            },
        )
    )

    hints = payload.get("feedback_hints")
    assert hints is not None
    assert hints["request_id"] == payload["request_id"]
    assert "lib/helper.py" in hints["paths_in_response"]
    accept = hints["commands"]["accept_helpful"]
    assert accept["command"] == "feedback_accept"
    assert accept["request_id"] == payload["request_id"]
