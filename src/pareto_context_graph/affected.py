"""Reverse structural walk to suggest tests for changed files."""

from __future__ import annotations

import sys
from collections import deque
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .blast import filter_existing
from .graph import get_changed_files
from .indexing import iter_indexable_files
from .structural import infer_test_target, is_test_path
from .store import Store

DEFAULT_TEST_GLOBS = (
    "*_test.go",
    "test_*.py",
    "tests/test_*.py",
    "*_test.py",
    "*.spec.ts",
    "*.test.ts",
    "*.spec.js",
    "*.test.js",
)


def matches_test_glob(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    normalized = path.replace("\\", "/")
    base = normalized.rsplit("/", 1)[-1]
    return any(fnmatch(base, pattern) or fnmatch(normalized, pattern) for pattern in patterns)


def _tests_for_impl(path: str, all_files: set[str]) -> set[str]:
    found: set[str] = set()
    for candidate in all_files:
        if not is_test_path(candidate) and not matches_test_glob(candidate, DEFAULT_TEST_GLOBS):
            continue
        target = infer_test_target(candidate, all_files)
        if target == path:
            found.add(candidate)
    return found


def compute_affected_tests(
    store: Store,
    repo_root: Path,
    changed_paths: list[str],
    *,
    max_depth: int = 3,
    test_globs: tuple[str, ...] | list[str] | None = None,
    import_kinds: set[str] | None = None,
) -> dict[str, Any]:
    """Walk reverse structural edges from changed files and collect test paths."""
    patterns = tuple(test_globs or DEFAULT_TEST_GLOBS)
    kinds = import_kinds or {"calls", "inherits", "tests"}
    all_files = set(iter_indexable_files(repo_root, store))
    existing_changed = filter_existing(repo_root, changed_paths)
    if not existing_changed:
        return {
            "changed": changed_paths,
            "related": [],
            "tests": [],
            "test_count": 0,
            "max_depth": max_depth,
        }

    visited: set[str] = set()
    related: list[dict[str, Any]] = []
    tests: set[str] = set()
    queue: deque[tuple[str, int]] = deque((path, 0) for path in existing_changed)

    while queue:
        path, depth = queue.popleft()
        if path in visited:
            continue
        visited.add(path)

        if is_test_path(path) or matches_test_glob(path, patterns):
            tests.add(path)
            related.append({"path": path, "depth": depth, "reason": "test_path"})
        else:
            related.append({"path": path, "depth": depth, "reason": "changed" if depth == 0 else "structural"})

        tests.update(_tests_for_impl(path, all_files))

        if depth >= max_depth:
            continue

        for src, kind, confidence in store.structural_incoming(path, kinds=kinds, limit=100):
            if src not in visited and src in all_files:
                related.append(
                    {
                        "path": src,
                        "depth": depth + 1,
                        "reason": f"incoming:{kind}",
                        "confidence": confidence,
                    }
                )
                if is_test_path(src) or matches_test_glob(src, patterns):
                    tests.add(src)
                queue.append((src, depth + 1))

    ordered_tests = sorted(tests)
    return {
        "changed": existing_changed,
        "related": related[:200],
        "tests": ordered_tests,
        "test_count": len(ordered_tests),
        "max_depth": max_depth,
        "test_globs": list(patterns),
    }


def affected_from_git(
    repo_root: Path,
    *,
    base: str = "main",
    paths: list[str] | None = None,
    max_depth: int = 3,
    test_globs: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    changed = paths if paths is not None else get_changed_files(repo_root, base=base)
    store = Store(repo_root)
    try:
        payload = compute_affected_tests(
            store,
            repo_root,
            changed,
            max_depth=max_depth,
            test_globs=test_globs,
        )
        payload["base"] = base
        return payload
    finally:
        store.close()


def read_paths_from_stdin() -> list[str]:
    lines = [line.strip() for line in sys.stdin if line.strip()]
    return lines
