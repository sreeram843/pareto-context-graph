"""Subsystem map: manual context-map + directory clusters (Phase 15.6)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .spec_drift import load_context_map
from .store import Store

AUTO_PREFIX_ROOTS = frozenset(
    {"src", "lib", "pkg", "tests", "test", "cmd", "internal", "api", "apps", "packages"}
)
DEFAULT_MIN_FILES = 5
DEFAULT_MAX_AUTO = 40
DEFAULT_FILE_LIMIT = 50


@dataclass
class Subsystem:
    key: str
    source: str
    files: list[str] = field(default_factory=list)
    specs: list[str] = field(default_factory=list)
    path_globs: list[str] = field(default_factory=list)

    def to_summary(self, *, top_hubs: list[dict[str, object]]) -> dict[str, Any]:
        return {
            "key": self.key,
            "source": self.source,
            "file_count": len(self.files),
            "specs": list(self.specs),
            "path_globs": list(self.path_globs),
            "top_hubs": top_hubs,
        }


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


def _auto_prefix(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] in AUTO_PREFIX_ROOTS:
        return f"{parts[0]}/{parts[1]}"
    if len(parts) >= 2 and parts[0] in AUTO_PREFIX_ROOTS:
        return parts[0]
    return parts[0] if parts else path


def _files_for_manual(meta: dict[str, Any], all_files: list[str]) -> list[str]:
    globs = meta.get("path_globs") or meta.get("paths") or []
    if not globs:
        return []
    matched = [
        path
        for path in all_files
        if any(_path_matches_glob(path, str(glob_pattern)) for glob_pattern in globs)
    ]
    return sorted(matched)


def _build_manual_subsystems(all_files: list[str], payload: dict[str, Any]) -> dict[str, Subsystem]:
    subsystems = payload.get("subsystems")
    if not isinstance(subsystems, dict):
        return {}
    out: dict[str, Subsystem] = {}
    for key, meta in subsystems.items():
        if not isinstance(meta, dict):
            continue
        files = _files_for_manual(meta, all_files)
        specs = [str(s) for s in (meta.get("specs") or [])]
        globs = [str(g) for g in (meta.get("path_globs") or meta.get("paths") or [])]
        out[str(key)] = Subsystem(
            key=str(key),
            source="manual",
            files=files,
            specs=specs,
            path_globs=globs,
        )
    return out


def _build_auto_subsystems(
    all_files: list[str],
    *,
    min_files: int = DEFAULT_MIN_FILES,
    max_subsystems: int = DEFAULT_MAX_AUTO,
    exclude_paths: set[str] | None = None,
) -> dict[str, Subsystem]:
    exclude_paths = exclude_paths or set()
    grouped: dict[str, list[str]] = {}
    for path in all_files:
        if path in exclude_paths:
            continue
        prefix = _auto_prefix(path)
        grouped.setdefault(prefix, []).append(path)

    ranked = sorted(
        ((prefix, files) for prefix, files in grouped.items() if len(files) >= min_files),
        key=lambda item: (-len(item[1]), item[0]),
    )[:max_subsystems]

    out: dict[str, Subsystem] = {}
    for prefix, files in ranked:
        out[prefix] = Subsystem(
            key=prefix,
            source="auto",
            files=sorted(files),
            path_globs=[f"{prefix}/**"] if "/" in prefix else [f"{prefix}/**"],
        )
    return out


def _top_hubs_for_files(
    store: Store, files: list[str], *, limit: int = 5
) -> list[dict[str, object]]:
    if not files:
        return []
    degrees = store.node_degrees()
    member = set(files)
    ranked = sorted(
        ((path, int(degrees.get(path, 0))) for path in member),
        key=lambda item: (-item[1], item[0]),
    )
    return [{"path": path, "degree": degree} for path, degree in ranked[:limit] if degree > 0]


def build_subsystem_registry(
    store: Store,
    repo_root: Path,
    *,
    min_files: int = DEFAULT_MIN_FILES,
    max_auto: int = DEFAULT_MAX_AUTO,
) -> dict[str, Subsystem]:
    """Merge manual context-map subsystems with directory-based auto clusters."""
    all_files = store.all_files()
    manual = _build_manual_subsystems(all_files, load_context_map(repo_root))
    manual_paths = {path for sub in manual.values() for path in sub.files}
    auto = _build_auto_subsystems(
        all_files,
        min_files=min_files,
        max_subsystems=max_auto,
        exclude_paths=manual_paths,
    )
    registry: dict[str, Subsystem] = dict(manual)
    for key, sub in auto.items():
        if key not in registry:
            registry[key] = sub
    return registry


def list_subsystems(
    store: Store,
    repo_root: Path,
    *,
    min_files: int = DEFAULT_MIN_FILES,
    max_auto: int = DEFAULT_MAX_AUTO,
) -> dict[str, Any]:
    registry = build_subsystem_registry(store, repo_root, min_files=min_files, max_auto=max_auto)
    items = []
    for key in sorted(registry.keys()):
        sub = registry[key]
        items.append(sub.to_summary(top_hubs=_top_hubs_for_files(store, sub.files)))
    items.sort(key=lambda row: (-int(row["file_count"]), str(row["key"])))
    manual_count = sum(1 for sub in registry.values() if sub.source == "manual")
    return {
        "subsystems": items,
        "count": len(items),
        "manual_count": manual_count,
        "auto_count": len(items) - manual_count,
    }


def subsystem_files(
    store: Store,
    repo_root: Path,
    key: str,
    *,
    file_limit: int = DEFAULT_FILE_LIMIT,
    min_files: int = DEFAULT_MIN_FILES,
    max_auto: int = DEFAULT_MAX_AUTO,
) -> dict[str, Any]:
    registry = build_subsystem_registry(store, repo_root, min_files=min_files, max_auto=max_auto)
    sub = registry.get(key)
    if sub is None:
        return {
            "error": "unknown_subsystem",
            "key": key,
            "available": sorted(registry.keys())[:20],
        }

    degrees = store.node_degrees()
    files = sorted(sub.files, key=lambda path: (-degrees.get(path, 0), path))[:file_limit]
    return {
        "key": sub.key,
        "source": sub.source,
        "specs": sub.specs,
        "path_globs": sub.path_globs,
        "file_count": len(sub.files),
        "files": files,
        "top_hubs": _top_hubs_for_files(store, sub.files),
    }
