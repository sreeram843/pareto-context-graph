"""Token estimation and savings calculation."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Conservative bytes-per-token ratio (works across languages)
BYTES_PER_TOKEN = 4
TOKENS_PER_BYTE_BY_SUFFIX = {
    ".py": 0.28,
    ".rb": 0.27,
    ".js": 0.28,
    ".ts": 0.28,
    ".tsx": 0.29,
    ".jsx": 0.29,
    ".java": 0.25,
    ".go": 0.25,
    ".rs": 0.26,
    ".sql": 0.24,
    ".yml": 0.31,
    ".yaml": 0.31,
    ".json": 0.35,
    ".md": 0.25,
    ".txt": 0.24,
}


def file_byte_count(repo_root: Path, paths: list[str]) -> int:
    """Sum byte sizes for a list of file paths."""
    total = 0
    for p in paths:
        fp = repo_root / p
        if fp.is_file():
            total += fp.stat().st_size
    return total


def repo_tracked_bytes(repo_root: Path) -> int:
    """Total bytes across all git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    files = [f for f in result.stdout.strip().splitlines() if f]
    return file_byte_count(repo_root, files)


def repo_tracked_count(repo_root: Path) -> int:
    """Number of git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    return len([f for f in result.stdout.strip().splitlines() if f])


def estimate_tokens(byte_count: int) -> int:
    """Convert bytes to estimated token count."""
    return byte_count // BYTES_PER_TOKEN


def estimate_file_tokens(file_path: Path) -> int:
    """Estimate tokens for a concrete file using suffix-aware calibration."""
    try:
        byte_count = file_path.stat().st_size
    except OSError:
        return 0
    ratio = TOKENS_PER_BYTE_BY_SUFFIX.get(file_path.suffix.lower())
    if ratio is None:
        return estimate_tokens(byte_count)
    return max(1, int(byte_count * ratio))


def compute_savings(
    repo_root: Path,
    blast_files: list[str],
) -> dict:
    """Compare full-repo tokens vs blast-radius tokens.

    Returns a dict with all metrics:
        full_bytes, full_tokens, full_files,
        blast_bytes, blast_tokens, blast_files,
        saved_tokens, percent_reduction, multiplier
    """
    full_bytes = repo_tracked_bytes(repo_root)
    full_files = repo_tracked_count(repo_root)
    full_tokens = estimate_tokens(full_bytes)

    # Filter to files that exist
    existing = [f for f in blast_files if (repo_root / f).is_file()]
    blast_bytes = file_byte_count(repo_root, existing)
    blast_tokens = sum(estimate_file_tokens(repo_root / f) for f in existing)

    saved_tokens = full_tokens - blast_tokens
    percent_reduction = (saved_tokens / full_tokens * 100) if full_tokens > 0 else 0
    multiplier = (full_tokens / blast_tokens) if blast_tokens > 0 else float("inf")

    return {
        "full_bytes": full_bytes,
        "full_tokens": full_tokens,
        "full_files": full_files,
        "blast_bytes": blast_bytes,
        "blast_tokens": blast_tokens,
        "blast_files": len(existing),
        "saved_tokens": saved_tokens,
        "percent_reduction": round(percent_reduction, 1),
        "multiplier": round(multiplier, 1),
    }
