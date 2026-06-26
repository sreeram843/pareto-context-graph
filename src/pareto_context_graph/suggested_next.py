"""Agent hints for tier escalation, compression, and session hygiene."""

from __future__ import annotations

from typing import Any


def build_suggested_next(
    *,
    tier: int,
    context_files: list[dict[str, Any]],
    compression: str,
    truncated: bool,
    timed_out_phase: str | None = None,
) -> dict[str, Any] | None:
    """Return the next recommended context call shape for the agent."""
    paths = [str(entry.get("path", "")) for entry in context_files if entry.get("path")]
    if truncated:
        return {
            "tier": tier,
            "paths": paths[:3],
            "reason": "truncated_or_timeout",
            "hint": (
                f"Retry with a smaller token_budget, fewer seed files, or timeout_ms; "
                f"phase={timed_out_phase or 'unknown'}"
            ),
        }

    if not paths:
        return None

    top_paths = paths[:3]
    if tier <= 1:
        return {
            "tier": 2,
            "paths": top_paths,
            "reason": "escalate_to_signatures",
            "hint": "Re-call context with tier=2 on these paths for function/class signatures.",
        }
    if tier == 2:
        return {
            "tier": 3,
            "paths": top_paths,
            "reason": "escalate_to_chunks",
            "hint": "Re-call context with tier=3 for implementation chunks on these paths.",
        }
    if tier >= 3 and compression in ("none", ""):
        return {
            "compression": "prune",
            "paths": top_paths,
            "reason": "shrink_payload",
            "hint": "Re-call with compression=prune; use retrieve + content_hash to restore verbatim text.",
        }
    return {
        "action": "feedback",
        "paths": top_paths,
        "reason": "mark_used_or_reject",
        "hint": "Call feedback_accept / feedback_reject on paths the agent actually used.",
    }
