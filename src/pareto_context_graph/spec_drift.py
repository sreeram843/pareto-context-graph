"""Warn when code changes without matching spec updates (Phase 15.2)."""

from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path
from typing import Any

DB_DIR = ".pareto-context-graph"
CONTEXT_MAP_FILE = "context-map.json"
DEFAULT_SINCE = "7 days ago"


def context_map_path(repo_root: Path) -> Path:
    return Path(repo_root) / DB_DIR / CONTEXT_MAP_FILE


def load_context_map(repo_root: Path) -> dict[str, Any]:
    path = context_map_path(repo_root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _git_commits_since(repo_root: Path, *, since: str) -> list[str]:
    result = subprocess.run(
        ["git", "log", f"--since={since}", "--pretty=format:%H"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _files_in_commit(repo_root: Path, commit: str) -> set[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _path_matches_glob(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    glob = pattern.replace("\\", "/").lstrip("./")
    if "**" in glob:
        prefix = glob.split("**", 1)[0].rstrip("/")
        if prefix and not normalized.startswith(prefix):
            return False
        suffix = glob.split("**", 1)[1].lstrip("/")
        if not suffix:
            return True
        return fnmatch.fnmatch(normalized, f"*{suffix}") or fnmatch.fnmatch(
            Path(normalized).name, suffix
        )
    return fnmatch.fnmatch(normalized, glob)


def check_spec_drift(
    repo_root: Path,
    *,
    since: str = DEFAULT_SINCE,
) -> dict[str, Any]:
    """Return warnings when subsystem code changed but linked specs did not."""
    repo_root = Path(repo_root)
    payload = load_context_map(repo_root)
    subsystems = payload.get("subsystems")
    if not isinstance(subsystems, dict) or not subsystems:
        return {
            "enabled": False,
            "reason": "no_context_map",
            "warnings": [],
            "since": since,
        }

    commits = _git_commits_since(repo_root, since=since)
    if not commits:
        return {"enabled": True, "warnings": [], "since": since, "commits": 0}

    warnings: list[dict[str, str]] = []
    seen_subsystems: set[str] = set()
    all_changed: set[str] = set()

    for commit in commits:
        changed = _files_in_commit(repo_root, commit)
        all_changed |= changed
        if not changed:
            continue

        for name, meta in subsystems.items():
            if not isinstance(meta, dict) or name in seen_subsystems:
                continue
            globs = meta.get("path_globs") or meta.get("paths") or []
            specs = meta.get("specs") or []
            if not globs or not specs:
                continue

            code_hits = [
                path for path in changed if any(_path_matches_glob(path, str(g)) for g in globs)
            ]
            if not code_hits:
                continue

            spec_paths = {str(s).replace("\\", "/").lstrip("./") for s in specs}
            spec_changed = any(
                path in spec_paths or any(path.endswith(spec) for spec in spec_paths)
                for path in changed
            )
            if spec_changed:
                continue

            seen_subsystems.add(name)
            warnings.append(
                {
                    "subsystem": str(name),
                    "code_files_changed": str(len(code_hits)),
                    "sample_paths": ", ".join(sorted(code_hits)[:3]),
                    "specs": ", ".join(sorted(spec_paths)[:3]),
                    "hint": (
                        f"Subsystem '{name}' code changed since {since} "
                        f"without a linked spec update in the same commit window."
                    ),
                }
            )

    return {
        "enabled": True,
        "since": since,
        "commits": len(commits),
        "changed_files": len(all_changed),
        "warnings": warnings,
    }
