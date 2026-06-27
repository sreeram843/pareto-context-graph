from __future__ import annotations

import subprocess
from pathlib import Path

PROFILES = {
    "tiny": {
        "commits": 5_000,
        "since": None,
        "shards": 1,
        "min_weight": 1,
        "max_depth": 3,
        "hub_penalty_strength": 1.0,
        "stage1_cap": 500,
        "expansion": "bfs",
        "iterations": 1,
        "half_life_days": 365,
        "prune_below": None,
        "mmr_lambda": 0.7,
    },
    "medium": {
        "commits": 20_000,
        "since": "24 months ago",
        "shards": 2,
        "min_weight": 1,
        "max_depth": 3,
        "hub_penalty_strength": 1.1,
        "stage1_cap": 500,
        "expansion": "bfs",
        "iterations": 1,
        "half_life_days": 270,
        "prune_below": None,
        "mmr_lambda": 0.7,
    },
    "large": {
        "commits": 50_000,
        "since": "18 months ago",
        "shards": 4,
        "min_weight": 2,
        "max_depth": 2,
        "hub_penalty_strength": 1.25,
        "stage1_cap": 650,
        "expansion": "bfs",
        "iterations": 1,
        "half_life_days": 220,
        "prune_below": 0.03,
        "mmr_lambda": 0.65,
    },
    "huge": {
        "commits": 20_000,
        "since": "12 months ago",
        "shards": 8,
        "min_weight": 3,
        "max_depth": 2,
        "hub_penalty_strength": 1.5,
        "stage1_cap": 800,
        "expansion": "rwr",
        "iterations": 2,
        "half_life_days": 180,
        "prune_below": 0.05,
        "mmr_lambda": 0.6,
        "max_files_per_commit": 50,
    },
    "huge-full": {
        "commits": 100_000,
        "since": "24 months ago",
        "shards": 8,
        "min_weight": 3,
        "max_depth": 2,
        "hub_penalty_strength": 1.5,
        "stage1_cap": 800,
        "expansion": "rwr",
        "iterations": 2,
        "half_life_days": 180,
        "prune_below": 0.05,
        "mmr_lambda": 0.6,
        "max_files_per_commit": 50,
    },
}


_autodetect_cache: dict[str, str | None] = {}


def autodetect_profile(repo_root: Path) -> str | None:
    key = str(repo_root.resolve())
    if key in _autodetect_cache:
        return _autodetect_cache[key]
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _autodetect_cache[key] = None
        return None
    try:
        count = int(result.stdout.strip())
    except ValueError:
        _autodetect_cache[key] = None
        return None
    if count > 100_000:
        profile = "huge"
    elif count > 50_000:
        profile = "large"
    elif count > 10_000:
        profile = "medium"
    else:
        profile = "tiny"
    _autodetect_cache[key] = profile
    return profile


def resolve_profile(name: str | None) -> dict:
    if not name:
        return {}
    return dict(PROFILES.get(name, {}))
