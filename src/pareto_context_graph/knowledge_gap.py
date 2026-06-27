"""Detect when graph retrieval is too weak — suggest codified specs (Phase 15.1)."""

from __future__ import annotations

from typing import Any


def build_knowledge_gap(
    *,
    confidence: dict[str, Any],
    query_only: bool,
    orchestrator_hit_count: int,
    files_included: int,
    seed_files: list[str],
) -> dict[str, Any] | None:
    """Return a hint when callers should document or seed before editing."""
    level = str(confidence.get("level", ""))
    score = float(confidence.get("score", 1.0))
    signals = list(confidence.get("signals") or [])

    reasons: list[str] = []
    if level == "low" or score < 0.45:
        reasons.append("low_retrieval_confidence")
    if query_only and orchestrator_hit_count == 0:
        reasons.append("query_first_no_hits")
    if not seed_files and files_included < 2:
        reasons.append("thin_result_set")
    if "no_orchestrator_hits" in signals:
        reasons.append("no_orchestrator_hits")

    if not reasons:
        return None

    hints: list[str] = []
    if "query_first_no_hits" in reasons or "no_orchestrator_hits" in reasons:
        hints.append(
            "Add seed files or a subsystem spec in .pareto-context-graph/context-map.json "
            "before large refactors."
        )
    if "low_retrieval_confidence" in reasons:
        hints.append(
            "Retrieval confidence is low; consider documenting the subsystem or re-building the graph."
        )
    if "thin_result_set" in reasons:
        hints.append("Very few files matched; verify query terms or provide explicit seed paths.")

    return {
        "signal": reasons[0],
        "reasons": reasons,
        "hint": " ".join(hints),
        "confidence_level": level,
        "confidence_score": score,
    }
