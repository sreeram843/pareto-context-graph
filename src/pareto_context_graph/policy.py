"""Org policy files for hooks, redaction, and context defaults (Phase 7.5, 13.5–13.6)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_TOKEN_BUDGET = 50_000


def org_policy_dir() -> Path:
    return Path(os.environ.get("PCG_ORG_POLICY_DIR", "/etc/pareto-context-graph"))


def policy_layer_paths(repo_root: Path) -> list[Path]:
    """Policy files in merge order (weakest → strongest)."""
    org = org_policy_dir()
    paths = [org / "policy.yaml", org / "policy.json"]
    env_path = os.environ.get("PCG_POLICY")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(repo_root / ".pareto-context-graph" / "policy.json")
    return paths


def _parse_policy_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError:
            return None
        loaded = yaml.safe_load(raw)
        return loaded if isinstance(loaded, dict) else {}
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return None


def _merge_list_union(base: list[Any], override: list[Any]) -> list[Any]:
    seen: set[str] = set()
    merged: list[Any] = []
    for item in base + override:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key == "allowed_hook_sha256":
            if isinstance(result.get(key), list) and isinstance(value, list):
                result[key] = _merge_list_union(result[key], value)
            else:
                result[key] = value
            continue
        if key == "hooks" and isinstance(value, dict):
            hooks_base = result.get("hooks")
            if not isinstance(hooks_base, dict):
                hooks_base = {}
            merged_hooks = dict(hooks_base)
            for hook_key, hook_value in value.items():
                if (
                    hook_key == "allowed_sha256"
                    and isinstance(merged_hooks.get("allowed_sha256"), list)
                    and isinstance(hook_value, list)
                ):
                    merged_hooks["allowed_sha256"] = _merge_list_union(
                        merged_hooks["allowed_sha256"], hook_value
                    )
                elif isinstance(hook_value, dict) and isinstance(merged_hooks.get(hook_key), dict):
                    merged_hooks[hook_key] = _deep_merge(merged_hooks[hook_key], hook_value)
                else:
                    merged_hooks[hook_key] = hook_value
            result["hooks"] = merged_hooks
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_policy(repo_root: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in policy_layer_paths(repo_root):
        if not path.is_file():
            continue
        payload = _parse_policy_file(path)
        if payload:
            merged = _deep_merge(merged, payload)
    return merged


def apply_context_policy(repo_root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply org/repo policy defaults and caps before context handling."""
    policy = load_policy(repo_root)
    args = dict(arguments)

    if policy.get("profile_default") and not args.get("profile"):
        args["profile"] = str(policy["profile_default"])

    if "tier" not in args and policy.get("default_tier") is not None:
        args["tier"] = int(policy["default_tier"])

    if "session_memory" not in args and policy.get("session_memory") is not None:
        args["session_memory"] = bool(policy["session_memory"])

    if "token_budget" not in args and policy.get("token_budget_default") is not None:
        args["token_budget"] = int(policy["token_budget_default"])

    max_tb = policy.get("max_token_budget")
    if max_tb is not None:
        current = int(args.get("token_budget", _DEFAULT_TOKEN_BUDGET))
        args["token_budget"] = min(current, int(max_tb))

    return args


def hook_allowed(repo_root: Path, hook_path: Path) -> bool:
    policy = load_policy(repo_root)
    allowed = policy.get("allowed_hook_sha256") or policy.get("hooks", {}).get("allowed_sha256")
    if not allowed:
        return True
    digest = hashlib.sha256(hook_path.read_bytes()).hexdigest()
    return digest in {str(item) for item in allowed}


def no_safety_allowed(repo_root: Path) -> bool:
    policy = load_policy(repo_root)
    return bool(policy.get("allow_no_safety", False))


def default_profile(repo_root: Path) -> str | None:
    policy = load_policy(repo_root)
    value = policy.get("profile_default")
    return str(value) if value else None
