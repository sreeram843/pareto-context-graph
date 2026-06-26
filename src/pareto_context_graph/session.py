"""Session memory: persist recent context paths and auto-fill ``already_have``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .features import feature_enabled
from .store import DB_DIR

SESSION_FILENAME = "session.json"
DEFAULT_MAX_PATHS = 100


def session_file(repo_root: Path) -> Path:
    return repo_root / DB_DIR / SESSION_FILENAME


def session_memory_enabled(arguments: dict) -> bool:
    if "session_memory" in arguments:
        return bool(arguments["session_memory"])
    return feature_enabled("SESSION_MEMORY")


def load_session_paths(repo_root: Path) -> list[str]:
    path = session_file(repo_root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return []
    return [str(p) for p in paths if p]


def merge_session_already_have(
    repo_root: Path,
    already_have: set[str],
    arguments: dict,
) -> tuple[set[str], int]:
    """Union session paths into ``already_have``; return merged set and new path count."""
    if not session_memory_enabled(arguments):
        return already_have, 0
    session_paths = set(load_session_paths(repo_root))
    if not session_paths:
        return already_have, 0
    added = session_paths - already_have
    return already_have | session_paths, len(added)


def record_session_paths(
    repo_root: Path,
    paths: list[str],
    *,
    max_paths: int = DEFAULT_MAX_PATHS,
) -> None:
    """Append returned paths to session memory (deduped, most recent last)."""
    if not paths:
        return
    existing = load_session_paths(repo_root)
    seen: set[str] = set()
    merged: list[str] = []
    for path in existing + [p for p in paths if p]:
        if path in seen:
            continue
        seen.add(path)
        merged.append(path)
    if max_paths > 0 and len(merged) > max_paths:
        merged = merged[-max_paths:]

    out = session_file(repo_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "paths": merged,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")


def clear_session(repo_root: Path) -> None:
    path = session_file(repo_root)
    if path.exists():
        path.unlink()
