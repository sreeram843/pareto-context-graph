"""Build symbol and BM25 content search indexes at graph build time."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .features import feature_enabled
from .store import Store
from .structural import extract_structural_edges
from .symbols import CODE_EXTENSIONS, extract_symbol_records

MAX_INDEX_BYTES = 50_000


def _git_tracked_files(repo_root: Path) -> list[str]:
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
        if Path(path).suffix.lower() in CODE_EXTENSIONS:
            paths.append(path)
    return paths


def iter_indexable_files(repo_root: Path, store: Store) -> list[str]:
    """Union of graph files and git-tracked source files."""
    paths = set(store.all_files())
    paths.update(_git_tracked_files(repo_root))
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
    if fp.suffix.lower() not in CODE_EXTENSIONS:
        stats["skipped"] += 1
        return

    signature = _file_signature(fp)
    if signature is None:
        stats["skipped"] += 1
        return

    store.clear_file_search_index(path)
    store.index_search_path(path)

    records = extract_symbol_records(fp, use_treesitter=feature_enabled("TREESITTER"))
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


def update_search_indexes(
    store: Store,
    repo_root: Path,
    *,
    paths: set[str] | None = None,
    full: bool = False,
) -> dict[str, int]:
    """Update search indexes for *paths* or all indexable files.

    When *full* is True, clears existing search indexes first. Otherwise only
    re-indexes paths whose on-disk mtime/size changed (or that lack index_state).
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

    all_files = set(iter_indexable_files(repo_root, store))
    if paths is not None:
        candidates = sorted(paths & all_files if paths else set())
    else:
        candidates = sorted(all_files)

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

    store.set_meta("search_index_version", "1", commit=False)
    store.commit()
    return stats


def rebuild_search_indexes(store: Store, repo_root: Path) -> dict[str, int]:
    """Full search-index rebuild (clears symbols, content, structural tables)."""
    return update_search_indexes(store, repo_root, full=True)
