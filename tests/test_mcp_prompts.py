"""MCP prompt template tests."""

from __future__ import annotations

import pytest

from pareto_context_graph.mcp_prompts import PROMPT_DESCRIPTORS, render_prompt


def test_prompt_descriptors_non_empty():
    names = {p["name"] for p in PROMPT_DESCRIPTORS}
    assert names == {
        "code_review",
        "debug_issue",
        "architecture_overview",
        "onboard_file",
        "pre_merge_check",
    }


def test_render_code_review():
    text = render_prompt("code_review", {"files": ["a.py", "b.py"], "query": "security"})
    assert "detect_changes" in text
    assert "a.py" in text
    assert "security" in text


def test_render_unknown_raises():
    with pytest.raises(KeyError):
        render_prompt("missing", {})
