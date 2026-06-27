"""Repo build/index configuration (.pareto-context-graph/config.json + defaults)."""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SearchIndexMode = Literal["lazy", "eager"]

DB_DIR = ".pareto-context-graph"
CONFIG_NAME = "config.json"

# Directory prefixes skipped even when git-tracked (CodeGraph-style defaults).
DEFAULT_EXCLUDE_DIR_PREFIXES: tuple[str, ...] = (
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    "target/",
    ".venv/",
    "venv/",
    "Pods/",
    ".next/",
    "__pycache__/",
    ".git/",
)

DEFAULT_MAX_FILE_BYTES = 1_048_576


@dataclass(frozen=True)
class RepoBuildConfig:
    exclude_patterns: tuple[str, ...] = ()
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    exclude_tests: bool = False
    search_index_mode: SearchIndexMode = "eager"

    def all_exclude_patterns(self) -> tuple[str, ...]:
        patterns = list(DEFAULT_EXCLUDE_DIR_PREFIXES)
        if self.exclude_tests:
            patterns.extend(("test/", "tests/", "**/test/", "**/tests/"))
        patterns.extend(self.exclude_patterns)
        return tuple(patterns)


def _config_path(repo_root: Path) -> Path:
    return Path(repo_root) / DB_DIR / CONFIG_NAME


def load_repo_config(
    repo_root: Path,
    *,
    profile_name: str | None = None,
) -> RepoBuildConfig:
    """Load config.json; apply profile defaults when present."""
    exclude: list[str] = []
    max_file_bytes = DEFAULT_MAX_FILE_BYTES
    exclude_tests = profile_name in {"huge", "huge-full"}

    path = _config_path(repo_root)
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            raw = {}
        if isinstance(raw.get("exclude"), list):
            exclude = [str(p) for p in raw["exclude"]]
        index_cfg = raw.get("index") if isinstance(raw.get("index"), dict) else {}
        if isinstance(index_cfg, dict) and index_cfg.get("max_file_bytes") is not None:
            try:
                max_file_bytes = int(index_cfg["max_file_bytes"])
            except (TypeError, ValueError):
                pass
        if "exclude_tests" in raw:
            exclude_tests = bool(raw["exclude_tests"])

    search_mode = _resolve_search_index_mode(raw, profile_name, None)

    return RepoBuildConfig(
        exclude_patterns=tuple(exclude),
        max_file_bytes=max_file_bytes,
        exclude_tests=exclude_tests,
        search_index_mode=search_mode,
    )


def _resolve_search_index_mode(
    raw: dict,
    profile_name: str | None,
    explicit: SearchIndexMode | None,
) -> SearchIndexMode:
    if explicit is not None:
        return explicit
    index_cfg = raw.get("index") if isinstance(raw.get("index"), dict) else {}
    if isinstance(index_cfg, dict):
        mode = index_cfg.get("search")
        if mode in ("lazy", "eager"):
            return mode
    if profile_name in {"huge", "huge-full"}:
        return "lazy"
    return "eager"


def resolve_search_index_mode(
    repo_root: Path,
    *,
    profile_name: str | None = None,
    explicit: SearchIndexMode | None = None,
) -> SearchIndexMode:
    """Resolve whether cold build runs search indexes eagerly or defers them."""
    if explicit is not None:
        return explicit
    if os.environ.get("PCG_BUILD_SEARCH_INDEX", "").strip().lower() in {"1", "true", "yes"}:
        return "eager"
    if os.environ.get("PCG_BUILD_SEARCH_INDEX", "").strip().lower() in {"0", "false", "no"}:
        return "lazy"
    path = _config_path(repo_root)
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            raw = {}
    return _resolve_search_index_mode(raw, profile_name, None)


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def path_excluded(path: str, config: RepoBuildConfig) -> bool:
    """Return True if *path* should be omitted from build/index passes."""
    norm = _normalize_path(path)
    if not norm or norm.startswith(f"{DB_DIR}/"):
        return True

    for prefix in DEFAULT_EXCLUDE_DIR_PREFIXES:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return True
        if f"/{prefix}" in f"/{norm}/":
            return True

    if config.exclude_tests:
        parts = norm.split("/")
        if "test" in parts or "tests" in parts:
            return True

    for pattern in config.exclude_patterns:
        pat = _normalize_path(pattern)
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, pat.rstrip("/")):
            return True
        if norm.startswith(pat.rstrip("/") + "/"):
            return True

    return False


def filter_paths(paths: list[str], repo_root: Path, config: RepoBuildConfig) -> list[str]:
    """Drop excluded paths; preserve order and uniqueness."""
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path in seen:
            continue
        if path_excluded(path, config):
            continue
        seen.add(path)
        out.append(path)
    return out


def file_too_large(repo_root: Path, path: str, config: RepoBuildConfig) -> bool:
    fp = Path(repo_root) / path
    try:
        return fp.stat().st_size > config.max_file_bytes
    except OSError:
        return True
