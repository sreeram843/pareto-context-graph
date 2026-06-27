"""Example hooks: run context before editing (Phase 15.4)."""

from __future__ import annotations


def pre_context(payload: dict) -> dict:
    """Ensure a modest budget and tier-1 map before the agent opens files."""
    payload.setdefault("tier", 1)
    payload.setdefault("token_budget", min(int(payload.get("token_budget", 8000)), 12000))
    payload.setdefault("timeout_ms", int(payload.get("timeout_ms", 5000)))
    return payload
