"""Learned prune biases from feedback — Phase D (11.5–11.6)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import DB_DIR

PRUNE_WEIGHTS_FILE = "prune_weights.json"
_FALSY = frozenset({"0", "false", "no", "off"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_prune_weights_cache: dict[str, float] | None = None
_prune_weights_repo: Path | None = None


def prune_weights_path(repo_root: Path) -> Path:
    return repo_root / DB_DIR / PRUNE_WEIGHTS_FILE


DEFAULT_TIER1_DROP_BIAS = -0.3
_KEEP_SIGNALS = frozenset({"query_first", "semantic", "orchestrator", "rwr"})


def learned_tier1_prune_enabled(repo_root: Path | None = None) -> bool:
    """Auto-on when prune_weights.json exists; opt out via PCG_FEATURE_LEARNED_TIER1_PRUNE=0."""
    raw = os.environ.get("PCG_FEATURE_LEARNED_TIER1_PRUNE")
    if raw is not None and raw.strip() != "":
        val = raw.strip().lower()
        if val in _FALSY:
            return False
        if val in _TRUTHY:
            return True
        return False
    if repo_root is not None and prune_weights_path(repo_root).is_file():
        return True
    return False


def tier1_keep_by_bias(
    path: str,
    prune_weights: dict[str, float],
    *,
    drop_below: float = DEFAULT_TIER1_DROP_BIAS,
) -> bool:
    """True when feedback bias does not warrant dropping this tier-1 row."""
    bias = prune_weights.get(path)
    if bias is None:
        return True
    return bias >= drop_below


def apply_learned_tier1_prune(
    context_files: list[dict],
    *,
    tier: int,
    prune_weights: dict[str, float],
    seed_files: list[str] | None = None,
    min_keep: int = 3,
    protect_top: int = 10,
    drop_below: float = DEFAULT_TIER1_DROP_BIAS,
) -> tuple[list[dict], dict]:
    """Drop tier-1 rows with strongly negative learned keep bias (Phase 11.6).

    Preserves seed files, retrieval signals, the first ``protect_top`` ranked rows,
    and at least ``min_keep`` rows (by original rank order).
    """
    if tier != 1 or not prune_weights:
        return context_files, {}

    seeds = set(seed_files or [])
    protected = context_files[: max(0, min(protect_top, len(context_files)))]
    tail = context_files[len(protected) :]
    kept_tail: list[dict] = []
    dropped_paths: list[str] = []

    for entry in tail:
        path = str(entry.get("path", ""))
        signal = str(entry.get("signal", ""))
        if (
            path in seeds
            or signal in _KEEP_SIGNALS
            or tier1_keep_by_bias(path, prune_weights, drop_below=drop_below)
        ):
            kept_tail.append(entry)
        else:
            dropped_paths.append(path)

    kept = list(protected) + kept_tail
    if len(kept) < min_keep and len(context_files) > len(kept):
        kept_paths = {str(entry.get("path", "")) for entry in kept}
        for entry in context_files:
            if len(kept) >= min_keep:
                break
            path = str(entry.get("path", ""))
            if path not in kept_paths:
                kept.append(entry)
                kept_paths.add(path)
                if path in dropped_paths:
                    dropped_paths.remove(path)

    meta: dict = {
        "dropped_count": len(dropped_paths),
        "kept_count": len(kept),
        "protected_top": len(protected),
        "drop_below": drop_below,
        "weighted_paths": sum(
            1 for entry in context_files if str(entry.get("path", "")) in prune_weights
        ),
    }
    if dropped_paths:
        meta["dropped_paths"] = dropped_paths[:15]
    return kept, meta


def learned_prune_enabled(repo_root: Path | None = None) -> bool:
    raw = os.environ.get("PCG_FEATURE_LEARNED_PRUNE")
    if raw is not None and raw.strip() != "":
        val = raw.strip().lower()
        if val in _FALSY:
            return False
        if val in _TRUTHY:
            return True
        return False
    if repo_root is not None and prune_weights_path(repo_root).is_file():
        return True
    return False


def learn_prune_weights(rows: list[tuple[str, int, int]]) -> dict[str, float]:
    """Map file path → keep bias in [-1, 1] from feedback used/total counts."""
    weights: dict[str, float] = {}
    for file_path, used_count, total_count in rows:
        total = max(1, int(total_count))
        ratio = float(int(used_count)) / total
        bias = max(-1.0, min(1.0, (ratio - 0.5) * 2.0))
        if abs(bias) < 0.05:
            continue
        weights[str(file_path)] = round(bias, 4)
    return weights


def save_prune_weights(repo_root: Path, weights: dict[str, float]) -> Path:
    path = prune_weights_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(weights, indent=2) + "\n")
    invalidate_prune_weights_cache()
    return path


def invalidate_prune_weights_cache() -> None:
    global _prune_weights_cache, _prune_weights_repo
    _prune_weights_cache = None
    _prune_weights_repo = None


def load_prune_weights(repo_root: Path) -> dict[str, float]:
    """Load per-file keep biases; empty when disabled or no artifact."""
    global _prune_weights_cache, _prune_weights_repo

    if not learned_prune_enabled(repo_root):
        return {}

    if _prune_weights_cache is not None and _prune_weights_repo == repo_root:
        return _prune_weights_cache

    path = prune_weights_path(repo_root)
    if not path.is_file():
        _prune_weights_cache = {}
        _prune_weights_repo = repo_root
        return _prune_weights_cache

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _prune_weights_cache = {}
        _prune_weights_repo = repo_root
        return _prune_weights_cache

    if not isinstance(payload, dict):
        _prune_weights_cache = {}
    else:
        _prune_weights_cache = {str(k): float(v) for k, v in payload.items()}
    _prune_weights_repo = repo_root
    return _prune_weights_cache
