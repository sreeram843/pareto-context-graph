"""Phase 15.7 codify suggestion tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from pareto_context_graph.codify_suggestion import (
    build_codify_suggestions,
    reject_counts_by_path,
)
from pareto_context_graph.feedback import record_feedback
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store

EXAMPLE_HOOK = Path(__file__).resolve().parents[1] / "docs/examples/hooks/feedback_hints.py"


def test_reject_counts_aggregate_per_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "src/noisy.py"
    for i in range(3):
        record_feedback(repo, kind="reject", request_id=f"req-{i}", paths=[path])
    counts = reject_counts_by_path(repo, since_days=7)
    assert counts[path] == 3


def test_build_codify_suggestions_threshold(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = "lib/helper.py"
    for i in range(2):
        record_feedback(repo, kind="reject", request_id=f"req-{i}", paths=[path])
    assert build_codify_suggestions(repo, [path], min_rejects=3) == []
    record_feedback(repo, kind="reject", request_id="req-3", paths=[path])
    suggestions = build_codify_suggestions(repo, [path], min_rejects=3)
    assert len(suggestions) == 1
    assert suggestions[0]["path"] == path
    assert suggestions[0]["reject_count"] == 3


def test_feedback_hints_includes_codify_after_repeated_rejects(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks = repo / ".pareto-context-graph" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(EXAMPLE_HOOK, hooks / "feedback_hints.py")

    seed = "app/main.py"
    noise = "lib/noisy.py"
    (repo / "app").mkdir(parents=True)
    (repo / seed).write_text("def main():\n    pass\n")
    (repo / "lib").mkdir(parents=True)
    (repo / noise).write_text("def noisy():\n    pass\n")

    store = Store(repo)
    store.record_co_change(seed, noise, weight=5.0)
    store.rebuild_top_neighbours(k=10)
    store.commit()
    store.close()

    for i in range(3):
        record_feedback(repo, kind="reject", request_id=f"reject-{i}", paths=[noise])

    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "context",
                "files": [seed],
                "query": "noisy",
                "tier": 1,
                "token_budget": 8000,
            },
        )
    )

    hints = payload.get("feedback_hints")
    assert hints is not None
    codify = hints.get("codify_suggestion")
    assert codify is not None
    assert codify["path"] == noise
    assert codify["reject_count"] >= 3
    assert "_repo_root" not in payload
