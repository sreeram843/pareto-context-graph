"""Tests for confidence calibration helpers."""

from pareto_context_graph.context_confidence import confidence_calibration_report


def test_confidence_calibration_report_correlates_score_and_recall():
    rows = [
        {"retrieval_confidence": {"score": 0.9}, "recall_at_5": 1.0},
        {"retrieval_confidence": {"score": 0.8}, "recall_at_5": 0.8},
        {"retrieval_confidence": {"score": 0.4}, "recall_at_5": 0.2},
        {"retrieval_confidence": {"score": 0.3}, "recall_at_5": 0.0},
    ]
    report = confidence_calibration_report(rows)
    assert report["cases"] == 4
    assert report["pearson_r"] > 0.9
    assert report["mean_abs_error"] < 0.2
