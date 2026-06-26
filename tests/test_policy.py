"""Tests for layered org/repo policy (Phase 13.5–13.6)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pareto_context_graph.hooks import load_hooks
from pareto_context_graph.policy import apply_context_policy, load_policy, no_safety_allowed

yaml = pytest.importorskip("yaml")


def _write_policy(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in {".yaml", ".yml"}:
        path.write_text(yaml.dump(payload, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_policy_layers_merge_org_yaml_and_repo_json(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    org_dir = tmp_path / "org"
    monkeypatch.setenv("PCG_ORG_POLICY_DIR", str(org_dir))

    _write_policy(
        org_dir / "policy.yaml",
        {
            "profile_default": "hub",
            "max_token_budget": 20_000,
            "allow_no_safety": False,
            "audit": {"max_bytes": 1000},
        },
    )
    _write_policy(
        repo / ".pareto-context-graph" / "policy.json",
        {
            "default_tier": 2,
            "session_memory": True,
            "allow_no_safety": True,
            "audit": {"max_files": 3},
        },
    )

    policy = load_policy(repo)
    assert policy["profile_default"] == "hub"
    assert policy["max_token_budget"] == 20_000
    assert policy["default_tier"] == 2
    assert policy["session_memory"] is True
    assert policy["allow_no_safety"] is True
    assert policy["audit"] == {"max_bytes": 1000, "max_files": 3}


def test_apply_context_policy_defaults_and_caps(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    org_dir = tmp_path / "org"
    monkeypatch.setenv("PCG_ORG_POLICY_DIR", str(org_dir))
    _write_policy(
        org_dir / "policy.yaml",
        {
            "default_tier": 2,
            "token_budget_default": 12_000,
            "max_token_budget": 15_000,
            "session_memory": True,
        },
    )

    applied = apply_context_policy(repo, {"query": "auth"})
    assert applied["tier"] == 2
    assert applied["token_budget"] == 12_000
    assert applied["session_memory"] is True

    capped = apply_context_policy(repo, {"query": "auth", "token_budget": 40_000})
    assert capped["token_budget"] == 15_000


def test_hook_allowlist_unions_across_layers(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    hooks = repo / ".pareto-context-graph" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "custom.py"
    hook.write_text("def pre_context(payload):\n    return payload\n")
    digest = hashlib.sha256(hook.read_bytes()).hexdigest()

    org_dir = tmp_path / "org"
    monkeypatch.setenv("PCG_ORG_POLICY_DIR", str(org_dir))
    _write_policy(org_dir / "policy.yaml", {"allowed_hook_sha256": ["deadbeef"]})
    _write_policy(repo / ".pareto-context-graph" / "policy.json", {"allowed_hook_sha256": [digest]})

    assert len(load_hooks(repo)) == 1
    assert no_safety_allowed(repo) is False


def test_env_policy_layer_overrides_org(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    org_dir = tmp_path / "org"
    env_policy = tmp_path / "fleet.json"
    monkeypatch.setenv("PCG_ORG_POLICY_DIR", str(org_dir))
    monkeypatch.setenv("PCG_POLICY", str(env_policy))

    _write_policy(org_dir / "policy.yaml", {"max_token_budget": 10_000})
    _write_policy(env_policy, {"max_token_budget": 25_000})
    _write_policy(repo / ".pareto-context-graph" / "policy.json", {"max_token_budget": 30_000})

    assert load_policy(repo)["max_token_budget"] == 30_000
