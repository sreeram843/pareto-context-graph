"""Dual-layer context response shape (Phase 15.8)."""

from __future__ import annotations

from typing import Any

_CODE_CONTEXT_OPTIONAL_KEYS = (
    "compression",
    "dropped_candidates",
    "truncated",
    "truncated_phase",
    "context_savings",
    "query_first",
    "selective_hybrid",
    "summary_prune",
    "learned_tier1_prune",
    "stage1_cap",
    "skipped_already_have",
    "session_already_have",
)


def apply_dual_layer_response(response: dict[str, Any]) -> dict[str, Any]:
    """Bump to response_version 3 with code_context + structured spec_context."""
    out = dict(response)
    out["response_version"] = 3

    raw_specs = out.get("spec_context")
    snippets: list[dict[str, Any]] | None = None
    if isinstance(raw_specs, list):
        snippets = raw_specs
    elif isinstance(raw_specs, dict):
        maybe = raw_specs.get("snippets")
        if isinstance(maybe, list):
            snippets = maybe

    code_context: dict[str, Any] = {
        "context_files": list(out.get("context_files") or []),
        "tier": out.get("tier"),
        "tokens_used": out.get("tokens_used"),
        "files_included": out.get("files_included"),
        "files_available": out.get("files_available"),
    }
    for key in _CODE_CONTEXT_OPTIONAL_KEYS:
        if key in out:
            code_context[key] = out[key]
    out["code_context"] = code_context

    if snippets:
        out["spec_context"] = {
            "snippets": snippets,
            "count": len(snippets),
        }
    else:
        out["spec_context"] = None

    return out
