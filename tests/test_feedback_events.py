"""Phase 5 feedback event log, fold, and counterfactual logging."""

from __future__ import annotations

import json

from pareto_context_graph.feedback import (
    FeedbackEventLog,
    fold_events_to_sqlite,
    log_context_request,
    record_feedback,
)
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.store import Store


def test_event_append_and_dedupe(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    logger = FeedbackEventLog(repo)

    assert logger.append(
        {"kind": "cite", "request_id": "req-1", "path": "a.py", "query": "q"},
        dedupe=True,
    )
    assert not logger.append(
        {"kind": "cite", "request_id": "req-1", "path": "a.py", "query": "q"},
        dedupe=True,
    )

    events = logger.read_all()
    assert len(events) == 1
    assert events[0]["kind"] == "cite"


def test_fold_events_positive_and_negative(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    logger = FeedbackEventLog(repo)
    logger.append(
        {"kind": "context_request", "request_id": "r1", "query": "auth", "candidates": []},
        dedupe=False,
    )
    logger.append({"kind": "accept", "request_id": "r1", "path": "auth.py", "query": "auth"}, dedupe=True)
    logger.append({"kind": "reject", "request_id": "r1", "path": "noise.py", "query": "auth"}, dedupe=True)
    logger.append(
        {"kind": "dwell", "request_id": "r1", "path": "short.py", "query": "auth", "dwell_seconds": 5},
        dedupe=True,
    )
    logger.append(
        {"kind": "dwell", "request_id": "r1", "path": "long.py", "query": "auth", "dwell_seconds": 45},
        dedupe=True,
    )

    stats = fold_events_to_sqlite(repo)
    assert stats["processed"] >= 3
    assert stats["positive"] >= 2
    assert stats["negative"] >= 1

    store = Store(repo)
    rows = store.conn.execute(
        "SELECT file_path, used FROM feedback ORDER BY file_path"
    ).fetchall()
    store.close()
    used_by_path = {path: bool(used) for path, used in rows}
    assert used_by_path["auth.py"] is True
    assert used_by_path["long.py"] is True
    assert used_by_path["noise.py"] is False
    assert "short.py" not in used_by_path


def test_log_context_request_writes_counterfactual(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log_context_request(
        repo,
        request_id="ctx-1",
        query="find router",
        seed_files=["main.py"],
        candidates=[{"path": "router.py", "score": 9.5, "features": {"bm25": 1.0}}],
        returned_paths=["router.py"],
    )
    events = FeedbackEventLog(repo).read_all()
    assert len(events) == 1
    assert events[0]["kind"] == "context_request"
    assert events[0]["returned_paths"] == ["router.py"]


def test_record_feedback_batch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    stats = record_feedback(
        repo,
        kind="cite",
        request_id="batch-1",
        paths=["a.py", "b.py"],
        query="q",
    )
    assert stats["written"] == 2
    assert stats["deduped"] == 0

    stats2 = record_feedback(
        repo,
        kind="cite",
        request_id="batch-1",
        paths=["a.py"],
        query="q",
    )
    assert stats2["written"] == 0
    assert stats2["deduped"] == 1


def test_mark_used_emits_positive_event(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo)
    store.log_feedback("q", "used.py", returned=True, used=False)
    store.close()

    payload = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {"command": "mark_used", "paths": ["used.py"], "request_id": "mu-1", "query": "q"},
        )
    )
    assert payload["updated"] >= 1
    assert payload["written"] == 1

    events = FeedbackEventLog(repo).read_all()
    assert any(e["kind"] == "mark_used" and e["path"] == "used.py" for e in events)


def test_feedback_mcp_commands(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    for command, kind in (
        ("feedback_cite", "cite"),
        ("feedback_accept", "accept"),
        ("feedback_reject", "reject"),
        ("feedback_view", "view"),
    ):
        payload = json.loads(
            _handle_tool_call(
                repo,
                "pareto_context_graph",
                {
                    "command": command,
                    "request_id": f"req-{kind}",
                    "paths": [f"{kind}.py"],
                    "query": "q",
                },
            )
        )
        assert payload["written"] == 1

    dwell = json.loads(
        _handle_tool_call(
            repo,
            "pareto_context_graph",
            {
                "command": "feedback_dwell",
                "request_id": "req-dwell",
                "paths": ["dwell.py"],
                "dwell_seconds": 60,
            },
        )
    )
    assert dwell["written"] == 1
