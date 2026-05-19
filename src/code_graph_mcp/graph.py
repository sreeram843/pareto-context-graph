"""Build a co-change graph from git history."""

from __future__ import annotations

import math
import os
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

from .store import Store

_NOISY_COMMIT_RE = re.compile(
    r"^(merge|bump|format|lint|rubocop|prettier|reformat|chore\(deps\))",
    re.IGNORECASE,
)


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
            header = token[len(marker):]
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


def _build_graph_legacy(
    repo_root: Path,
    *,
    max_commits: int,
    since: str | None,
    max_files_per_commit: int,
) -> Store:
    store = Store(repo_root)
    store.clear()

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

    processed = 0
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
            store.record_co_change(a, b, weight=pair_weight, last_seen_ts=commit_ts)
        processed += 1

    store.set_meta("last_build_commits", str(processed))
    store.set_meta("total_commits_scanned", str(len(commits)))
    store.set_meta("last_build_since", since or "")
    store.set_meta("build_strategy", "legacy-v1")
    if commits:
        store.set_meta("last_commit_hash", commits[0][0])
    store.rebuild_top_neighbours(k=50)
    store.commit()
    return store


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
    commits = _iter_commits_streaming(
        repo_root,
        max_commits=max_commits,
        since=since,
    )

    processed = 0
    for commit_hash, commit_ts, subject, files in commits:
        if len(files) > max_files_per_commit or len(files) < 2:
            continue
        pair_weight = _commit_pair_weight(len(files), subject)
        if pair_weight <= 0:
            continue
        for a, b in combinations(sorted(files), 2):
            store.record_co_change(a, b, weight=pair_weight, last_seen_ts=commit_ts)
        processed += 1

    store.set_meta("last_build_commits", str(processed))
    store.set_meta("total_commits_scanned", str(len(commits)))
    store.set_meta("last_build_since", since or "")
    store.set_meta("build_strategy", "streaming-v1")
    if commits:
        store.set_meta("last_commit_hash", commits[0][0])
    store.rebuild_top_neighbours(k=50)
    store.commit()
    return store


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

    # Collect all commits up-front so we can split into chronological buckets
    commits = _iter_commits_streaming(repo_root, max_commits=max_commits, since=since)
    if not commits:
        store = Store(repo_root)
        store.clear()
        store.set_meta("build_strategy", f"sharded-v1:{shards}")
        store.set_meta("last_build_commits", "0")
        store.set_meta("total_commits_scanned", "0")
        store.set_meta("last_build_since", since or "")
        store.commit()
        return store

    # Split into roughly equal chronological chunks
    chunk_size = max(1, math.ceil(len(commits) / shards))
    chunks = [commits[i : i + chunk_size] for i in range(0, len(commits), chunk_size)]

    # Aggregate pairs in parallel; fall back to sequential on any failure
    merged: dict[tuple[str, str], tuple[float, int]] = {}
    try:
        with ProcessPoolExecutor(max_workers=min(shards, len(chunks))) as pool:
            futures = [
                pool.submit(_aggregate_stream_chunk, chunk, max_files_per_commit)
                for chunk in chunks
            ]
            shard_results = [f.result() for f in futures]
    except Exception:
        shard_results = [
            _aggregate_stream_chunk(chunk, max_files_per_commit) for chunk in chunks
        ]

    for shard in shard_results:
        for key, (weight, ts) in shard.items():
            if key in merged:
                merged[key] = (merged[key][0] + weight, max(merged[key][1], ts))
            else:
                merged[key] = (weight, ts)

    store = Store(repo_root)
    store.clear()
    for (a, b), (weight, ts) in merged.items():
        store.record_co_change(a, b, weight=weight, last_seen_ts=ts)
    store.set_meta("total_commits_scanned", str(len(commits)))
    store.set_meta("last_build_since", since or "")
    store.set_meta("build_strategy", f"sharded-v1:{shards}")
    if commits:
        store.set_meta("last_commit_hash", commits[0][0])
    store.rebuild_top_neighbours(k=50)
    store.commit()
    return store


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

    for commit_hash in commits:
        subject = _run_git(["log", "--format=%s", "-n", "1", commit_hash], cwd=repo_root).strip()
        diff_output = _run_git(
            ["diff-tree", "--root", "--no-commit-id", "-r", "--name-only", commit_hash],
            cwd=repo_root,
        )
        # Deduplicate paths (git may report the same file twice for mode changes)
        files = list(dict.fromkeys(f for f in diff_output.strip().splitlines() if f))
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
            store.record_co_change(a, b, weight=pair_weight, last_seen_ts=commit_ts)

    store.set_meta("last_commit_hash", commits[0])
    store.rebuild_top_neighbours(k=50)
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
