"""Build a co-change graph from git history."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

from .build_profile import META_KEY, BuildTimings
from .indexing import rebuild_search_indexes, update_search_indexes
from .store import Store

_NOISY_COMMIT_RE = re.compile(
    r"^(merge|bump|format|lint|rubocop|prettier|reformat|chore\(deps\))",
    re.IGNORECASE,
)
_EDGE_FLUSH_SIZE = 10_000
_COMMIT_CACHE_NAME = "commit_window_cache.json"
_COMMIT_CACHE_VERSION = 2
_COMMIT_CACHE_TTL_SECONDS = int(os.environ.get("PCG_COMMIT_CACHE_TTL_SECONDS", str(7 * 24 * 3600)))
BUILD_WINDOW_META = "build_window_key"


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _run_git_bytes(args: list[str], cwd: Path) -> bytes:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def _commit_pair_weight(file_count: int, subject: str) -> float:
    """Compute a contribution weight for a commit's file pairs."""
    if file_count < 2:
        return 0.0

    weight = 1.0 / math.log(file_count + 1)
    if _NOISY_COMMIT_RE.match(subject.strip()):
        weight *= 0.25
    return max(weight, 0.05)


def _iter_commits_streaming(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
) -> list[tuple[str, int, str, list[str]]]:
    marker = "__CG__"
    args = [
        "log",
        "--name-only",
        "-z",
        "--no-renames",
        "--no-merges",
        f"--format={marker}%H%x09%ct%x09%s",
        f"-{max_commits}",
    ]
    if since:
        args.append(f"--since={since}")

    raw = _run_git_bytes(args, cwd=repo_root).decode("utf-8", errors="replace")
    tokens = raw.split("\x00")

    commits: list[tuple[str, int, str, list[str]]] = []
    current_hash = ""
    current_ts = 0
    current_subject = ""
    current_files: list[str] = []

    def _flush() -> None:
        nonlocal current_hash, current_ts, current_subject, current_files
        if current_hash:
            commits.append(
                (
                    current_hash,
                    current_ts,
                    current_subject,
                    list(dict.fromkeys(f for f in current_files if f)),
                )
            )

    for token in tokens:
        if not token:
            continue
        if token.startswith(marker):
            _flush()
            header = token[len(marker) :]
            commit_hash, _, rest = header.partition("\t")
            ts_str, _, subject = rest.partition("\t")
            current_hash = commit_hash
            try:
                current_ts = int(ts_str)
            except ValueError:
                current_ts = 0
            current_subject = subject
            current_files = []
        else:
            cleaned = token.strip()
            if cleaned:
                current_files.append(cleaned)

    _flush()
    return commits


def _head_sha(repo_root: Path) -> str:
    return _run_git(["rev-parse", "HEAD"], cwd=repo_root).strip()


def _window_key(max_commits: int, since: str | None, shards: int) -> str:
    return f"v1|{since or ''}|{max_commits}|{shards}"


def _commit_cache_path(repo_root: Path) -> Path:
    return repo_root / ".pareto-context-graph" / _COMMIT_CACHE_NAME


def _load_cached_commits(
    repo_root: Path,
    window_key: str,
) -> list[tuple[str, int, str, list[str]]] | None:
    path = _commit_cache_path(repo_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if payload.get("window_key") != window_key:
        return None
    if int(payload.get("cache_version", 1)) < _COMMIT_CACHE_VERSION:
        return None
    cached_at = payload.get("cached_at")
    if cached_at is not None and time.time() - float(cached_at) > _COMMIT_CACHE_TTL_SECONDS:
        return None
    return [(item[0], int(item[1]), item[2], list(item[3])) for item in payload.get("commits", [])]


def _save_cached_commits(
    repo_root: Path,
    window_key: str,
    commits: list[tuple[str, int, str, list[str]]],
) -> None:
    path = _commit_cache_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = [[h, ts, subj, files] for h, ts, subj, files in commits]
    path.write_text(
        json.dumps(
            {
                "cache_version": _COMMIT_CACHE_VERSION,
                "cached_at": time.time(),
                "window_key": window_key,
                "commits": serializable,
            },
            separators=(",", ":"),
        )
    )


def _get_commits_for_window(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
    window_key: str,
    timings: BuildTimings | None = None,
) -> list[tuple[str, int, str, list[str]]]:
    cached = _load_cached_commits(repo_root, window_key)
    if cached is not None:
        if timings is not None:
            timings.meta["commit_cache"] = "hit"
        return cached
    started = timings.start("git_log") if timings is not None else None
    commits = _iter_commits_streaming(
        repo_root,
        max_commits=max_commits,
        since=since,
    )
    if timings is not None and started is not None:
        timings.stop("git_log", started)
        timings.meta["commit_cache"] = "miss"
    _save_cached_commits(repo_root, window_key, commits)
    return commits


def _maybe_skip_build(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
    shards: int,
) -> Store | None:
    """Return existing store if noop; run incremental update when HEAD advanced."""
    head = _head_sha(repo_root)
    window_key = _window_key(max_commits, since, shards)
    store = Store(repo_root)
    if store.file_count() == 0:
        store.close()
        return None
    if store.get_meta(BUILD_WINDOW_META) != window_key:
        store.close()
        return None
    last_hash = store.get_meta("last_commit_hash")
    if not last_hash:
        store.close()
        return None
    if last_hash == head:
        store.set_meta("build_status", "noop", commit=False)
        store.commit()
        return store

    store.close()
    return incremental_update(repo_root, since_commit=last_hash)


def _finalize_store(
    store: Store,
    repo_root: Path,
    timings: BuildTimings,
    *,
    processed: int | None = None,
    commits_scanned: int | None = None,
    since: str | None = None,
    build_strategy: str,
    last_commit_hash: str | None = None,
    window_key: str | None = None,
) -> Store:
    """Shared post-processing: indexes, metadata, commit."""
    if processed is not None:
        store.set_meta("last_build_commits", str(processed), commit=False)
    if commits_scanned is not None:
        store.set_meta("total_commits_scanned", str(commits_scanned), commit=False)
    store.set_meta("last_build_since", since or "", commit=False)
    store.set_meta("build_strategy", build_strategy, commit=False)
    if last_commit_hash:
        store.set_meta("last_commit_hash", last_commit_hash, commit=False)
    if window_key:
        store.set_meta(BUILD_WINDOW_META, window_key, commit=False)
    store.set_meta("build_status", "built", commit=False)

    started = timings.start("top_neighbours")
    store.rebuild_top_neighbours(k=50)
    timings.stop("top_neighbours", started)

    started = timings.start("search_indexes")
    index_stats = rebuild_search_indexes(store, repo_root)
    timings.stop("search_indexes", started)
    timings.meta["search_index_stats"] = index_stats

    started = timings.start("files_fts")
    store.rebuild_files_fts()
    timings.stop("files_fts", started)

    started = timings.start("commit")
    store.set_meta(META_KEY, timings.to_json(), commit=False)
    store.commit()
    timings.stop("commit", started)
    return store


def _build_graph_legacy(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
    max_files_per_commit: int,
) -> Store:
    store = Store(repo_root)
    store.clear()
    timings = BuildTimings(meta={"profile": "legacy-v1"})
    started = timings.start("git_log")
    log_args = ["log", "--format=%H%x09%ct%x09%s", f"-{max_commits}", "--no-merges"]
    if since:
        log_args.append(f"--since={since}")
    log_output = _run_git(log_args, cwd=repo_root)

    commits: list[tuple[str, int, str]] = []
    for line in log_output.strip().splitlines():
        if not line:
            continue
        commit_hash, _, rest = line.partition("\t")
        ts_str, _, subject = rest.partition("\t")
        try:
            commit_ts = int(ts_str)
        except ValueError:
            commit_ts = 0
        commits.append((commit_hash, commit_ts, subject))

    timings.stop("git_log", started)
    processed = 0
    started = timings.start("sqlite_writes")
    edge_batch: list[tuple[str, str, float, int]] = []

    def _flush_edges() -> None:
        if edge_batch:
            store.record_co_changes_bulk(edge_batch)
            edge_batch.clear()

    for commit_hash, commit_ts, subject in commits:
        diff_output = _run_git(
            ["diff-tree", "--root", "--no-commit-id", "-r", "--name-only", commit_hash],
            cwd=repo_root,
        )
        files = list(dict.fromkeys(f for f in diff_output.strip().splitlines() if f))
        if len(files) > max_files_per_commit or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        for a, b in combinations(sorted(files), 2):
            edge_batch.append((a, b, pair_weight, commit_ts))
            if len(edge_batch) >= _EDGE_FLUSH_SIZE:
                _flush_edges()
        processed += 1
    _flush_edges()
    timings.stop("sqlite_writes", started)

    return _finalize_store(
        store,
        repo_root,
        timings,
        processed=processed,
        commits_scanned=len(commits),
        since=since,
        build_strategy="legacy-v1",
        last_commit_hash=commits[0][0] if commits else None,
        window_key=_window_key(max_commits, since, 1),
    )


def build_graph(
    repo_root: Path,
    *,
    max_commits: int = 5000,
    since: str | None = None,
    max_files_per_commit: int = 250,
) -> Store:
    """Parse git log and populate the co-change graph.

    Args:
        repo_root: Path to the git repository root.
        max_commits: How far back in history to look.
        since: Optional git --since expression (for example: "12 months ago",
            "2025-01-01"). When provided, history is bounded by both `since`
            and `max_commits`.
        max_files_per_commit: Hard safety cap for pathological commits. Large
            commits are down-weighted rather than skipped outright.
    """
    if os.getenv("CODE_GRAPH_LEGACY_BUILD") == "1":
        return _build_graph_legacy(
            repo_root,
            max_commits=max_commits,
            since=since,
            max_files_per_commit=max_files_per_commit,
        )

    store = Store(repo_root)
    store.clear()
    timings = BuildTimings(meta={"profile": "streaming-v1"})
    window_key = _window_key(max_commits, since, 1)
    commits = _get_commits_for_window(
        repo_root,
        max_commits=max_commits,
        since=since,
        window_key=window_key,
        timings=timings,
    )

    processed = 0
    started = timings.start("sqlite_writes")
    edge_batch: list[tuple[str, str, float, int]] = []

    def _flush_edges() -> None:
        if edge_batch:
            store.record_co_changes_bulk(edge_batch)
            edge_batch.clear()

    for commit_hash, commit_ts, subject, files in commits:
        if len(files) > max_files_per_commit or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        for a, b in combinations(sorted(files), 2):
            edge_batch.append((a, b, pair_weight, commit_ts))
            if len(edge_batch) >= _EDGE_FLUSH_SIZE:
                _flush_edges()
        processed += 1
    _flush_edges()
    timings.stop("sqlite_writes", started)

    return _finalize_store(
        store,
        repo_root,
        timings,
        processed=processed,
        commits_scanned=len(commits),
        since=since,
        build_strategy="streaming-v1",
        last_commit_hash=commits[0][0] if commits else None,
        window_key=window_key,
    )


def _compute_shard_pairs(
    repo_root: str,
    commit_hashes: list[str],
    max_files_per_commit: int,
) -> dict[tuple[str, str], tuple[float, int]]:
    root = Path(repo_root)
    pairs: dict[tuple[str, str], tuple[float, int]] = {}
    for commit_hash in commit_hashes:
        subject = _run_git(["log", "--format=%s", "-n", "1", commit_hash], cwd=root).strip()
        ts_raw = _run_git(["log", "--format=%ct", "-n", "1", commit_hash], cwd=root).strip()
        try:
            commit_ts = int(ts_raw)
        except ValueError:
            commit_ts = 0
        diff_output = _run_git(
            ["diff-tree", "--root", "--no-commit-id", "-r", "--name-only", commit_hash],
            cwd=root,
        )
        files = list(dict.fromkeys(f for f in diff_output.strip().splitlines() if f))
        if len(files) > max_files_per_commit or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        for a, b in combinations(sorted(files), 2):
            key = (a, b)
            current = pairs.get(key)
            if current is None:
                pairs[key] = (pair_weight, commit_ts)
            else:
                pairs[key] = (current[0] + pair_weight, max(current[1], commit_ts))
    return pairs


def _aggregate_stream_chunk(
    commits: list[tuple[str, int, str, list[str]]],
    max_files_per_commit: int,
) -> dict[tuple[str, str], tuple[float, int]]:
    pairs: dict[tuple[str, str], tuple[float, int]] = {}
    for _commit_hash, commit_ts, subject, files in commits:
        if len(files) > max_files_per_commit or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        for a, b in combinations(sorted(files), 2):
            key = (a, b)
            current = pairs.get(key)
            if current is None:
                pairs[key] = (pair_weight, commit_ts)
            else:
                pairs[key] = (current[0] + pair_weight, max(current[1], commit_ts))
    return pairs


def build_graph_sharded(
    repo_root: Path,
    *,
    max_commits: int = 5000,
    since: str | None = None,
    max_files_per_commit: int = 250,
    shards: int = 1,
) -> Store:
    shards = max(1, int(shards))
    skipped = _maybe_skip_build(
        repo_root,
        max_commits=max_commits,
        since=since,
        shards=shards,
    )
    if skipped is not None:
        return skipped

    if shards == 1:
        store = build_graph(
            repo_root,
            max_commits=max_commits,
            since=since,
            max_files_per_commit=max_files_per_commit,
        )
        store.set_meta("build_strategy", "sharded-v1:1")
        store.commit()
        return store

    timings = BuildTimings(meta={"profile": f"sharded-v1:{shards}", "shards": shards})
    window_key = _window_key(max_commits, since, shards)
    commits = _get_commits_for_window(
        repo_root,
        max_commits=max_commits,
        since=since,
        window_key=window_key,
        timings=timings,
    )
    if not commits:
        store = Store(repo_root)
        store.clear()
        timings.meta["commits_scanned"] = 0
        return _finalize_store(
            store,
            repo_root,
            timings,
            processed=0,
            commits_scanned=0,
            since=since,
            build_strategy=f"sharded-v1:{shards}",
            window_key=window_key,
        )

    chunk_size = max(1, math.ceil(len(commits) / shards))
    chunks = [commits[i : i + chunk_size] for i in range(0, len(commits), chunk_size)]

    started = timings.start("pair_aggregate")
    merged: dict[tuple[str, str], tuple[float, int]] = {}
    try:
        with ProcessPoolExecutor(max_workers=min(shards, len(chunks))) as pool:
            futures = [
                pool.submit(_aggregate_stream_chunk, chunk, max_files_per_commit)
                for chunk in chunks
            ]
            shard_results = [f.result() for f in futures]
    except Exception:
        shard_results = [_aggregate_stream_chunk(chunk, max_files_per_commit) for chunk in chunks]

    for shard in shard_results:
        for key, (weight, ts) in shard.items():
            if key in merged:
                merged[key] = (merged[key][0] + weight, max(merged[key][1], ts))
            else:
                merged[key] = (weight, ts)
    timings.stop("pair_aggregate", started)
    timings.meta["unique_pairs"] = len(merged)
    timings.meta["commits_scanned"] = len(commits)

    store = Store(repo_root)
    store.clear()
    started = timings.start("sqlite_writes")
    bulk_rows = [(a, b, weight, ts) for (a, b), (weight, ts) in merged.items()]
    store.record_co_changes_bulk(bulk_rows)
    timings.stop("sqlite_writes", started)

    processed = sum(
        1
        for _commit_hash, _commit_ts, subject, files in commits
        if 2 <= len(files) <= max_files_per_commit and _commit_pair_weight(len(files), subject) > 0
    )
    return _finalize_store(
        store,
        repo_root,
        timings,
        processed=processed,
        commits_scanned=len(commits),
        since=since,
        build_strategy=f"sharded-v1:{shards}",
        last_commit_hash=commits[0][0],
        window_key=window_key,
    )


def incremental_update(repo_root: Path, since_commit: str | None = None) -> Store:
    """Update the graph with only new commits since last build."""
    store = Store(repo_root)

    if since_commit is None:
        since_commit = store.get_meta("last_commit_hash")
    if since_commit is None:
        return build_graph(repo_root)

    log_output = _run_git(
        ["log", "--format=%H", f"{since_commit}..HEAD", "--no-merges"],
        cwd=repo_root,
    )
    commits = [h for h in log_output.strip().splitlines() if h]

    if not commits:
        return store

    edge_batch: list[tuple[str, str, float, int]] = []
    touched_paths: set[str] = set()

    for commit_hash in commits:
        subject = _run_git(["log", "--format=%s", "-n", "1", commit_hash], cwd=repo_root).strip()
        diff_output = _run_git(
            ["diff-tree", "--root", "--no-commit-id", "-r", "--name-only", commit_hash],
            cwd=repo_root,
        )
        files = list(dict.fromkeys(f for f in diff_output.strip().splitlines() if f))
        touched_paths.update(files)
        if len(files) > 250 or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        ts_raw = _run_git(["log", "--format=%ct", "-n", "1", commit_hash], cwd=repo_root).strip()
        try:
            commit_ts = int(ts_raw)
        except ValueError:
            commit_ts = 0
        for a, b in combinations(sorted(files), 2):
            edge_batch.append((a, b, pair_weight, commit_ts))

    if edge_batch:
        store.record_co_changes_bulk(edge_batch)

    store.set_meta("last_commit_hash", commits[0], commit=False)
    store.rebuild_top_neighbours(k=50)
    if touched_paths:
        update_search_indexes(store, repo_root, paths=touched_paths)
    store.commit()
    return store


def decay_sweep(
    repo_root: Path,
    *,
    half_life_days: float,
    prune_below: float | None = None,
) -> Store:
    store = Store(repo_root)
    deleted = store.apply_decay(half_life_days=half_life_days, prune_below=prune_below)
    if prune_below is not None:
        store.set_meta("last_prune_below", str(prune_below))
    store.set_meta("last_decay_half_life_days", str(half_life_days))
    store.set_meta("last_decay_deleted", str(deleted))
    store.rebuild_top_neighbours(k=50)
    store.commit()
    return store


def get_changed_files(repo_root: Path, base: str = "main") -> list[str]:
    """Return files changed between base branch and HEAD."""
    try:
        output = _run_git(["diff", "--name-only", f"{base}...HEAD"], cwd=repo_root)
    except RuntimeError:
        # Fallback if base doesn't exist
        output = _run_git(["diff", "--name-only", "HEAD~1"], cwd=repo_root)
    return [f for f in output.strip().splitlines() if f]
