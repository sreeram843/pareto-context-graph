"""Phase 15 codified-context bridge tests."""

from __future__ import annotations

import json
from pathlib import Path

from pareto_context_graph.context_confidence import build_retrieval_confidence
from pareto_context_graph.graph import build_graph
from pareto_context_graph.knowledge_gap import build_knowledge_gap
from pareto_context_graph.routing_hints import build_routing_hints
from pareto_context_graph.server import _handle_tool_call
from pareto_context_graph.spec_drift import check_spec_drift


def test_knowledge_gap_on_low_confidence():
    confidence = build_retrieval_confidence(
        sparse_graph=True,
        truncated=True,
        timed_out_phase="rank",
        query_only=True,
        orchestrator_hit_count=0,
        files_included=1,
    )
    gap = build_knowledge_gap(
        confidence=confidence,
        query_only=True,
        orchestrator_hit_count=0,
        files_included=1,
        seed_files=[],
    )
    assert gap is not None
    assert "low_retrieval_confidence" in gap["reasons"]
    assert gap["hint"]


def test_knowledge_gap_absent_when_strong():
    confidence = build_retrieval_confidence(
        sparse_graph=False,
        truncated=False,
        timed_out_phase="",
        query_only=False,
        orchestrator_hit_count=4,
        files_included=6,
    )
    assert (
        build_knowledge_gap(
            confidence=confidence,
            query_only=False,
            orchestrator_hit_count=4,
            files_included=6,
            seed_files=["src/a.py"],
        )
        is None
    )


def test_spec_drift_warns_when_code_changes_without_spec(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src" / "auth").mkdir(parents=True)
    (repo / "src" / "auth" / "login.py").write_text("def login(): pass\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "auth.md").write_text("# auth\n")

    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    pcg = repo / ".pareto-context-graph"
    pcg.mkdir()
    (pcg / "context-map.json").write_text(
        json.dumps(
            {
                "subsystems": {
                    "auth": {
                        "path_globs": ["src/auth/**"],
                        "specs": ["docs/auth.md"],
                    }
                }
            }
        )
    )

    (repo / "src" / "auth" / "login.py").write_text("def login():\n    return True\n")
    subprocess.run(["git", "add", "src/auth/login.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "change auth"], cwd=repo, check=True, capture_output=True
    )

    report = check_spec_drift(repo, since="30 days ago")
    assert report["enabled"] is True
    assert len(report["warnings"]) == 1
    assert report["warnings"][0]["subsystem"] == "auth"


def test_routing_hints_match_intent(tmp_path):
    pcg = tmp_path / ".pareto-context-graph"
    pcg.mkdir()
    (pcg / "routing.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "openapi",
                        "match": {"intent": "openapi"},
                        "suggest": {"hint": "openapi rules"},
                    }
                ]
            }
        )
    )
    hints = build_routing_hints(
        tmp_path,
        query="openapi swagger schema",
        returned_paths=["fastapi/openapi/models.py"],
    )
    assert hints and hints[0]["rule_id"] == "openapi"


def test_context_includes_phase15_fields(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=60, files=12, seed=3)
    store = build_graph(repo, max_commits=80)
    store.close()

    pcg = repo / ".pareto-context-graph"
    (pcg / "routing.json").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "default_docs",
                        "match": {"path_prefix": "src"},
                        "suggest": {"hint": "check subsystem docs"},
                    }
                ]
            }
        )
    )

    raw = _handle_tool_call(
        repo,
        "pareto_context_graph",
        {
            "command": "context",
            "files": ["src/a.py"],
            "query": "unknownzzzz topic with no hits",
            "tier": 1,
            "token_budget": 8000,
            "query_first": True,
            "session_memory": False,
        },
    )
    payload = json.loads(raw)
    assert "retrieval_confidence" in payload
    assert "routing_hints" in payload or payload.get("context_files")


def test_doctor_includes_spec_drift(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=40, files=8, seed=2)
    store = build_graph(repo, max_commits=60)
    store.close()

    payload = json.loads(_handle_tool_call(repo, "pareto_context_graph", {"command": "doctor"}))
    assert "spec_drift" in payload
