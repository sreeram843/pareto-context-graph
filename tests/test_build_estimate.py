"""Phase 10.4 build estimate and doctor report tests."""

from __future__ import annotations

import json

from pareto_context_graph.build_estimate import (
    BuildInputs,
    BuildPlan,
    estimate_build,
    format_disk,
    format_duration,
    gather_doctor_report,
)
from pareto_context_graph.graph import build_graph
from pareto_context_graph.server import _handle_tool_call


def test_estimate_anchors_near_measured_baselines():
    cases = [
        (5_000, 3_500, 17.0, 24.0),
        (5_150, 10_500, 792.0, 289.0),
        (100_000, 40_000, 37_877.0, 1_200.0),
    ]
    for commits, files, build_sec, db_mb in cases:
        inputs = BuildInputs(
            plan=BuildPlan("huge", commits, None, 8),
            commits_in_window=commits,
            tracked_source_files=files,
            total_commits=commits,
        )
        est = estimate_build(inputs)
        mid_build = est["build_seconds"]["mid"]
        mid_db = est["graph_db_mb"]["mid"]
        assert abs(mid_build - build_sec) / build_sec < 0.05
        assert abs(mid_db - db_mb) / db_mb < 0.08


def test_format_helpers():
    assert format_duration(45) == "45s"
    assert format_duration(120) == "2.0 min"
    assert format_duration(10_800) == "3.0 h"
    assert format_disk(512) == "~512 MB"
    assert format_disk(1_200) == "~1.2 GB"


def test_doctor_command_returns_build_estimate(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=50, files=10, seed=5)
    store = build_graph(repo, max_commits=100)
    store.close()

    payload = json.loads(_handle_tool_call(repo, "pareto_context_graph", {"command": "doctor"}))
    assert "files" in payload
    assert "edges" in payload
    assert "top_hubs" in payload
    assert "build_estimate" in payload
    est = payload["build_estimate"]
    assert est["commits_in_window"] > 0
    assert est["tracked_source_files"] > 0
    assert est["build_seconds"]["mid"] > 0
    assert est["graph_db_mb"]["mid"] > 0
    assert est["build_human"]


def test_doctor_estimate_before_build(synthetic_repo_factory):
    repo = synthetic_repo_factory(commits=30, files=8, seed=6)
    report = gather_doctor_report(repo, profile="tiny", commits=100)
    est = report["build_estimate"]
    assert est["profile"] == "tiny"
    assert est["commits_cap"] == 100
    assert est["build_seconds"]["mid"] < 120
