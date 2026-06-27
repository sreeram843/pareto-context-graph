"""Build symbol and BM25 content search indexes at graph build time."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .repo_config import file_too_large, load_repo_config, path_excluded
from .spec_index import rebuild_spec_indexes, update_spec_indexes
from .store import Store
from .structural import extract_structural_edges
from .symbols import CODE_EXTENSIONS, extract_symbol_records

MAX_INDEX_BYTES = 50_000
SEARCH_INDEX_STATUS_META = "search_index_status"
INDEX_COMMIT_BATCH = max(1, int(os.environ.get("PCG_INDEX_COMMIT_BATCH", "100")))


def _git_tracked_files(repo_root: Path, *, profile_name: str | None = None) -> list[str]:
    config = load_repo_config(repo_root, profile_name=profile_name)
    try:
        output = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    paths: list[str] = []
    for path in output.splitlines():
        if not path or path.startswith(".pareto-context-graph/"):
            continue
        if path_excluded(path, config):
            continue
        if Path(path).suffix.lower() not in CODE_EXTENSIONS:
            continue
        if file_too_large(repo_root, path, config):
            continue
        paths.append(path)
    return paths


def iter_indexable_files(
    repo_root: Path, store: Store, *, profile_name: str | None = None
) -> list[str]:
    """Union of graph files and git-tracked source files."""
    config = load_repo_config(repo_root, profile_name=profile_name)
    paths = {p for p in store.all_files() if not path_excluded(p, config)}
    paths.update(_git_tracked_files(repo_root, profile_name=profile_name))
    return sorted(paths)


def _file_signature(fp: Path) -> tuple[int, int] | None:
    try:
        stat = fp.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def _index_one_file(
    store: Store,
    repo_root: Path,
    path: str,
    all_files: set[str],
    stats: dict[str, int],
) -> None:
    fp = repo_root / path
    if not fp.is_file():
        stats["skipped"] += 1
        return
    config = load_repo_config(repo_root)
    if path_excluded(path, config) or file_too_large(repo_root, path, config):
        stats["skipped"] += 1
        return
    if fp.suffix.lower() not in CODE_EXTENSIONS:
        stats["skipped"] += 1
        return

    signature = _file_signature(fp)
    if signature is None:
        stats["skipped"] += 1
        return

    store.clear_file_search_index(path)
    store.index_search_path(path)

    records = extract_symbol_records(fp)
    if records:
        store.index_file_symbols(path, records)
        stats["symbols"] += len(records)

    try:
        body = fp.read_text(errors="ignore")[:MAX_INDEX_BYTES]
    except OSError:
        body = ""
    store.index_file_content(path, body)
    stats["content_files"] += 1

    for edge in extract_structural_edges(fp, path, all_files):
        store.add_structural_edge(
            edge["src_path"],
            edge["dst_path"],
            edge["kind"],
            edge.get("confidence", "INFERRED"),
        )
        stats["structural_edges"] += 1

    mtime_ns, size = signature
    store.set_index_state(path, mtime_ns, size)
    stats["indexed"] += 1


def list_pending_index_paths(
    store: Store,
    repo_root: Path,
    *,
    profile_name: str | None = None,
    limit: int | None = None,
) -> list[str]:
    pending: list[str] = []
    for path in iter_indexable_files(repo_root, store, profile_name=profile_name):
        fp = repo_root / path
        if not fp.is_file() or fp.suffix.lower() not in CODE_EXTENSIONS:
            continue
        signature = _file_signature(fp)
        if signature is None:
            continue
        if store.get_index_state(path) != signature:
            pending.append(path)
            if limit is not None and len(pending) >= limit:
                break
    return pending


def count_pending_index_files(
    store: Store,
    repo_root: Path,
    *,
    profile_name: str | None = None,
) -> int:
    return len(list_pending_index_paths(store, repo_root, profile_name=profile_name))


def _set_search_index_status(store: Store, repo_root: Path, *, profile_name: str | None) -> str:
    pending = count_pending_index_files(store, repo_root, profile_name=profile_name)
    status = "complete" if pending == 0 else "partial"
    store.set_meta(SEARCH_INDEX_STATUS_META, status, commit=False)
    return status


def update_search_indexes(
    store: Store,
    repo_root: Path,
    *,
    paths: set[str] | None = None,
    full: bool = False,
    profile_name: str | None = None,
) -> dict[str, int]:
    """Update search indexes for *paths* or all indexable files.

    When *full* is True, clears existing search indexes first. Otherwise only
    re-indexes paths whose on-disk mtime/size changed (or that lack index_state).
    Commits every ``INDEX_COMMIT_BATCH`` files so interrupted runs can resume.
    """
    stats = {
        "symbols": 0,
        "content_files": 0,
        "structural_edges": 0,
        "skipped": 0,
        "indexed": 0,
        "unchanged": 0,
    }
    if full:
        store.clear_search_indexes()
        if store._table_exists("index_state"):
            store.conn.execute("DELETE FROM index_state")

    all_files = set(iter_indexable_files(repo_root, store, profile_name=profile_name))
    if paths is not None:
        candidates = sorted(paths & all_files if paths else set())
    else:
        candidates = sorted(all_files)

    batch = 0
    for path in candidates:
        fp = repo_root / path
        if not fp.is_file() or fp.suffix.lower() not in CODE_EXTENSIONS:
            stats["skipped"] += 1
            continue
        signature = _file_signature(fp)
        if signature is None:
            stats["skipped"] += 1
            continue
        if not full:
            prior = store.get_index_state(path)
            if prior == signature:
                stats["unchanged"] += 1
                continue
        _index_one_file(store, repo_root, path, all_files, stats)
        batch += 1
        if batch >= INDEX_COMMIT_BATCH:
            store.set_meta("search_index_version", "1", commit=False)
            store.commit()
            batch = 0

    store.set_meta("search_index_version", "1", commit=False)
    _set_search_index_status(store, repo_root, profile_name=profile_name)
    store.commit()
    return stats


def rebuild_search_indexes(
    store: Store,
    repo_root: Path,
    *,
    profile_name: str | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Full search-index rebuild (clears symbols, content, structural tables)."""
    return update_search_indexes(
        store,
        repo_root,
        full=force,
        profile_name=profile_name,
    )


def ensure_search_indexes(
    store: Store,
    repo_root: Path,
    *,
    profile_name: str | None = None,
    force: bool = False,
    include_specs: bool = True,
) -> dict[str, int | str | bool]:
    """Build or resume deferred search indexes (Phase 2 of a lazy cold build)."""
    stats = update_search_indexes(
        store,
        repo_root,
        full=force,
        profile_name=profile_name,
    )
    status = store.get_meta(SEARCH_INDEX_STATUS_META) or "partial"
    if include_specs and status == "complete":
        update_spec_indexes(store, repo_root)
        store.commit()
    return {
        **stats,
        "search_index_status": status,
        "resumed": not force and stats.get("unchanged", 0) > 0,
    }
