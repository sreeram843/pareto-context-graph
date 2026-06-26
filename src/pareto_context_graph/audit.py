"""Append-only audit log for context/search calls (Phase 7.6)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .policy import load_policy
from .store import DB_DIR

DEFAULT_AUDIT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_AUDIT_MAX_FILES = 5

_AUDIT_LOCK = threading.Lock()


def _audit_path(repo_root: Path) -> Path:
    custom = os.environ.get("PCG_AUDIT_LOG")
    if custom:
        return Path(custom).expanduser()
    return repo_root / DB_DIR / "audit.jsonl"


def audit_rotation_config(repo_root: Path) -> dict[str, int | bool]:
    """Resolved rotation policy: env overrides repo policy, then defaults."""
    if os.environ.get("PCG_AUDIT_ROTATION", "").lower() in {"0", "false", "no"}:
        return {"enabled": False, "max_bytes": 0, "max_files": 0}

    policy = load_policy(repo_root)
    audit = policy.get("audit") if isinstance(policy.get("audit"), dict) else {}
    max_bytes = int(
        os.environ.get("PCG_AUDIT_MAX_BYTES") or audit.get("max_bytes") or DEFAULT_AUDIT_MAX_BYTES
    )
    max_files = int(
        os.environ.get("PCG_AUDIT_MAX_FILES") or audit.get("max_files") or DEFAULT_AUDIT_MAX_FILES
    )
    enabled = max_bytes > 0 and max_files > 1
    return {
        "enabled": enabled,
        "max_bytes": max_bytes if enabled else 0,
        "max_files": max_files if enabled else 0,
    }


def _rotate_audit_log(path: Path, *, max_bytes: int, max_files: int) -> bool:
    """Rotate when ``path`` is at or above ``max_bytes``. Returns True if rotated."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return False

    parent = path.parent
    base = path.name
    oldest = parent / f"{base}.{max_files - 1}"
    if oldest.exists():
        oldest.unlink()
    for idx in range(max_files - 2, 0, -1):
        src = parent / f"{base}.{idx}"
        if not src.exists():
            continue
        src.rename(parent / f"{base}.{idx + 1}")
    path.rename(parent / f"{base}.1")
    return True


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def log_audit_event(
    repo_root: Path,
    *,
    command: str,
    query: str = "",
    returned_paths: int = 0,
    tokens_used: int = 0,
    truncated: bool = False,
    request_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    if os.environ.get("PCG_DISABLE_AUDIT", "").lower() in {"1", "true", "yes"}:
        return
    event: dict[str, Any] = {
        "ts": int(time.time()),
        "command": command,
        "user": os.environ.get("USER") or os.environ.get("USERNAME") or "",
        "repo": str(repo_root),
        "query_hash": _query_hash(query) if query else "",
        "returned_paths": int(returned_paths),
        "tokens_used": int(tokens_used),
        "truncated": bool(truncated),
        "request_id": request_id,
    }
    if extra:
        event.update(extra)
    path = _audit_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    rotation = audit_rotation_config(repo_root)
    with _AUDIT_LOCK:
        if rotation["enabled"]:
            _rotate_audit_log(
                path,
                max_bytes=int(rotation["max_bytes"]),
                max_files=int(rotation["max_files"]),
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
