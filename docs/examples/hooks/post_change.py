"""Example hooks: surface feedback + routing after context (Phase 15.4)."""

from __future__ import annotations


def post_context(response: dict) -> dict:
    """Attach lightweight reminders when PCG signals gaps or routing hints."""
    extras: dict = {}
    if response.get("knowledge_gap"):
        extras["pre_change_reminder"] = (
            "Document or seed the subsystem before large edits (see knowledge_gap.hint)."
        )
    if response.get("routing_hints"):
        extras["routing_reminders"] = [
            str(h.get("hint", "")) for h in response["routing_hints"] if h.get("hint")
        ]
    if extras:
        response["workflow_hints"] = extras
    return response
