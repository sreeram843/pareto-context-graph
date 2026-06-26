"""Phase 11.5: feedback-derived ranker features."""

from __future__ import annotations

from pareto_context_graph.feedback import FeedbackEventLog, feedback_path_signals
from pareto_context_graph.ranker import FEATURE_KEYS
from pareto_context_graph.context_ranking import candidate_features as _candidate_features


def test_feedback_path_signals_aggregate_dwell_and_reject(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    log = FeedbackEventLog(repo)
    log.append(
        {"kind": "dwell", "request_id": "r1", "path": "auth.py", "dwell_seconds": 45},
        dedupe=False,
    )
    log.append(
        {"kind": "reject", "request_id": "r1", "path": "noise.py"},
        dedupe=False,
    )
    signals = feedback_path_signals(repo)
    assert signals["auth.py"]["dwell_seconds"] == 45.0
    assert signals["noise.py"]["rejected"] == 1.0


def test_candidate_features_include_feedback_and_already_have():
    row = {"path": "auth.py", "weight": 3, "_features": {"symbol": 2.0}}
    feats = _candidate_features(
        row,
        files=[],
        node_degrees={"auth.py": 1},
        learned={},
        embed_scores={},
        hub_penalty_strength=1.0,
        already_have={"auth.py"},
        feedback_signals={"auth.py": {"dwell_seconds": 30.0, "rejected": 0.0}},
    )
    assert feats["was_in_already_have"] == 1.0
    assert feats["dwell_seconds"] == 30.0
    assert feats["rejected"] == 0.0
    for key in ("was_in_already_have", "dwell_seconds", "rejected"):
        assert key in FEATURE_KEYS
