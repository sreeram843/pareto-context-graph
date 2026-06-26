"""Tests for learned prune weights (Phase D)."""

from __future__ import annotations

import json
from pathlib import Path

from pareto_context_graph.payload_compress import prune_body, prune_context_entry
from pareto_context_graph.prune_learn import (
    apply_learned_tier1_prune,
    learn_prune_weights,
    load_prune_weights,
    save_prune_weights,
    tier1_keep_by_bias,
)


def test_learn_prune_weights_maps_used_ratio_to_bias():
    rows = [
        ("src/good.py", 8, 10),
        ("src/bad.py", 1, 10),
        ("src/neutral.py", 5, 10),
    ]
    weights = learn_prune_weights(rows)
    assert weights["src/good.py"] > 0.3
    assert weights["src/bad.py"] < -0.3
    assert "src/neutral.py" not in weights


def test_save_and_load_prune_weights(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    weights = {"src/a.py": 0.8, "src/b.py": -0.7}
    save_prune_weights(repo, weights)
    loaded = load_prune_weights(repo)
    assert loaded == weights


def test_positive_bias_keeps_more_lines_than_negative():
    body = "\n".join(
        [
            "def authenticate(user):",
            "    token = issue_token(user)",
            "    cache.set(user.id, token)",
            "    audit.log('login', user)",
            "    metrics.increment('auth')",
            "    return token",
            "def helper():",
            "    return 1",
        ]
    )
    positive = prune_body(body, "authenticate token", keep_bias=0.8)
    negative = prune_body(body, "authenticate token", keep_bias=-0.8)
    assert len(positive.splitlines()) >= len(negative.splitlines())


def test_prune_context_entry_uses_keep_bias():
    entry = {
        "path": "src/auth.py",
        "content": "def login():\n" + "    step()\n" * 30,
    }
    positive = prune_context_entry(entry, "login", keep_bias=0.8)
    negative = prune_context_entry(entry, "login", keep_bias=-0.8)
    assert len(positive["content"].splitlines()) >= len(negative["content"].splitlines())


def test_apply_learn_writes_prune_weights(tmp_path: Path):
    from pareto_context_graph.feedback import FeedbackEventLog, record_feedback
    from pareto_context_graph.feedback_replay import apply_learn

    repo = tmp_path / "repo"
    repo.mkdir()
    record_feedback(repo, kind="accept", request_id="r1", paths=["src/good.py"])
    record_feedback(repo, kind="reject", request_id="r1", paths=["src/bad.py"])
    FeedbackEventLog(repo).append(
        {
            "kind": "context_request",
            "request_id": "r1",
            "query": "auth",
            "candidates": [{"path": "src/good.py", "features": {}}],
            "returned_paths": ["src/good.py"],
        },
        dedupe=False,
    )

    result = apply_learn(repo)
    assert result["prune_weights"] >= 1
    path = repo / ".pareto-context-graph" / "prune_weights.json"
    assert path.is_file()
    payload = json.loads(path.read_text())
    assert isinstance(payload, dict)


def test_tier1_keep_by_bias_drops_strongly_negative():
    weights = {"src/good.py": 0.8, "src/bad.py": -0.7, "src/neutral.py": 0.0}
    assert tier1_keep_by_bias("src/good.py", weights) is True
    assert tier1_keep_by_bias("src/bad.py", weights) is False
    assert tier1_keep_by_bias("src/neutral.py", weights) is True
    assert tier1_keep_by_bias("src/unknown.py", weights) is True


def test_apply_learned_tier1_prune_drops_negative_bias_tail():
    files = [
        {"path": "fastapi/routing.py", "summary": "APIRouter", "tokens_actual": 40},
        {"path": "docs/logo.md", "summary": "branding", "tokens_actual": 30},
        {"path": "fastapi/security/oauth2.py", "summary": "OAuth2", "tokens_actual": 35},
        {"path": "noise/extra.py", "summary": "misc", "tokens_actual": 20},
    ]
    weights = {
        "fastapi/routing.py": 0.8,
        "fastapi/security/oauth2.py": 0.6,
        "docs/logo.md": -0.8,
        "noise/extra.py": -0.7,
    }
    kept, meta = apply_learned_tier1_prune(
        files,
        tier=1,
        prune_weights=weights,
        protect_top=1,
        min_keep=2,
    )
    paths = [entry["path"] for entry in kept]
    assert paths[0] == "fastapi/routing.py"
    assert "docs/logo.md" not in paths
    assert meta["dropped_count"] >= 1
    assert len(kept) >= 2


def test_apply_learned_tier1_prune_skips_non_tier1():
    files = [{"path": "a.py", "summary": "x", "content": "body"}]
    kept, meta = apply_learned_tier1_prune(files, tier=3, prune_weights={"a.py": -0.9})
    assert kept == files
    assert meta == {}
