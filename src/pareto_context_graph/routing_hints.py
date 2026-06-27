"""Repo-local routing hints: intent/path → specialist suggestions (Phase 15.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .taxonomy import classify_query_intent

DB_DIR = ".pareto-context-graph"
ROUTING_FILE = "routing.json"


def routing_path(repo_root: Path) -> Path:
    return Path(repo_root) / DB_DIR / ROUTING_FILE


def load_routing_rules(repo_root: Path) -> list[dict[str, Any]]:
    path = routing_path(repo_root)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rules = payload.get("rules") if isinstance(payload, dict) else None
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def _path_prefix_match(paths: list[str], prefix: str) -> bool:
    needle = prefix.replace("\\", "/").rstrip("/")
    if not needle:
        return False
    for path in paths:
        normalized = path.replace("\\", "/")
        if normalized == needle or normalized.startswith(needle + "/"):
            return True
    return False


def _rule_matches(
    rule: dict[str, Any],
    *,
    intent: str,
    paths: list[str],
    max_hub_degree: int,
) -> bool:
    match = rule.get("match")
    if not isinstance(match, dict):
        return False
    if "intent" in match and str(match["intent"]) != intent:
        return False
    if "path_prefix" in match and not _path_prefix_match(paths, str(match["path_prefix"])):
        return False
    if "path_contains" in match:
        needle = str(match["path_contains"])
        if not any(needle in p for p in paths):
            return False
    min_deg = match.get("hub_degree_gte")
    if min_deg is not None and max_hub_degree < int(min_deg):
        return False
    return True


def build_routing_hints(
    repo_root: Path,
    *,
    query: str,
    returned_paths: list[str],
    seed_paths: list[str] | None = None,
    hub_degrees: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Return matching routing suggestions from routing.json."""
    rules = load_routing_rules(repo_root)
    if not rules:
        return []

    intent = classify_query_intent(query)
    paths = list(dict.fromkeys((seed_paths or []) + returned_paths))
    degrees = hub_degrees or {}
    max_hub = 0
    for path in paths:
        max_hub = max(max_hub, int(degrees.get(path, 0)))

    hints: list[dict[str, Any]] = []
    for rule in rules:
        if not _rule_matches(rule, intent=intent, paths=paths, max_hub_degree=max_hub):
            continue
        suggest = rule.get("suggest")
        if not isinstance(suggest, dict):
            continue
        entry: dict[str, Any] = {"rule_id": rule.get("id", "unnamed"), **suggest}
        if "hint" not in entry:
            continue
        hints.append(entry)
    return hints[:5]
