"""BM25 index for codified context: docs, rules, and agent manifests (Phase 15.5)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .spec_drift import load_context_map
from .store import Store

SPEC_SUFFIXES = frozenset({".md", ".mdc", ".txt", ".rst"})
SPEC_ROOT_FILES = frozenset(
    {
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        ".cursorrules",
    }
)
SPEC_DIR_PREFIXES = (
    "docs/",
    "doc/",
    ".cursor/rules/",
    ".github/",
)
MAX_SPEC_BYTES = 80_000
MAX_SPEC_FILES = 300
SNIPPET_CHARS = 480


def classify_spec_kind(path: str) -> str:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    if name == "AGENTS.md" or "agents" in normalized.lower():
        return "agents"
    if normalized.startswith(".cursor/rules/") or normalized.endswith(".mdc"):
        return "rule"
    return "doc"


def is_spec_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith(".pareto-context-graph/"):
        return False
    name = Path(normalized).name
    if name in SPEC_ROOT_FILES:
        return True
    suffix = Path(normalized).suffix.lower()
    if suffix not in SPEC_SUFFIXES:
        return False
    return any(
        normalized.startswith(prefix) or normalized == prefix.rstrip("/")
        for prefix in SPEC_DIR_PREFIXES
    )


def _git_tracked_spec_files(repo_root: Path) -> list[str]:
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
    paths = [line.strip() for line in output.splitlines() if line.strip()]
    return [path for path in paths if is_spec_path(path)]


def _context_map_spec_paths(repo_root: Path) -> list[str]:
    payload = load_context_map(repo_root)
    subsystems = payload.get("subsystems")
    if not isinstance(subsystems, dict):
        return []
    out: list[str] = []
    for meta in subsystems.values():
        if not isinstance(meta, dict):
            continue
        for spec in meta.get("specs") or []:
            path = str(spec).replace("\\", "/").lstrip("./")
            if path:
                out.append(path)
    return out


def discover_spec_files(repo_root: Path) -> list[str]:
    """Collect spec/doc paths to index (git-tracked + context-map + on-disk fallbacks)."""
    repo_root = Path(repo_root)
    found: dict[str, None] = {}

    for path in _context_map_spec_paths(repo_root):
        if (repo_root / path).is_file():
            found[path] = None

    for path in _git_tracked_spec_files(repo_root):
        found[path] = None

    if not found:
        for prefix in SPEC_DIR_PREFIXES:
            base = repo_root / prefix.rstrip("/")
            if not base.is_dir():
                continue
            for fp in base.rglob("*"):
                if not fp.is_file():
                    continue
                rel = fp.relative_to(repo_root).as_posix()
                if is_spec_path(rel):
                    found[rel] = None
        for name in SPEC_ROOT_FILES:
            fp = repo_root / name
            if fp.is_file():
                found[name] = None

    paths = sorted(found.keys())
    if len(paths) > MAX_SPEC_FILES:
        priority = set(_context_map_spec_paths(repo_root))
        priority.update(name for name in SPEC_ROOT_FILES if name in found)
        ranked = [p for p in paths if p in priority]
        ranked.extend(p for p in paths if p not in priority)
        paths = ranked[:MAX_SPEC_FILES]
    return paths


def extract_spec_title(body: str, *, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def spec_snippet(body: str, query: str, *, max_chars: int = SNIPPET_CHARS) -> str:
    terms = [t.lower() for t in re.findall(r"[a-zA-Z]\w{2,}", query)][:6]
    lines = body.splitlines()
    best = ""
    best_score = -1
    for idx, line in enumerate(lines):
        lower = line.lower()
        score = sum(1 for term in terms if term in lower)
        if score > best_score:
            window = "\n".join(lines[idx : idx + 4]).strip()
            if window:
                best = window
                best_score = score
    if not best:
        best = "\n".join(lines[:4]).strip()
    if len(best) > max_chars:
        return best[: max_chars - 3] + "..."
    return best


def _file_signature(fp: Path) -> tuple[int, int] | None:
    try:
        stat = fp.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def _index_one_spec(store: Store, repo_root: Path, path: str, stats: dict[str, int]) -> None:
    fp = repo_root / path
    if not fp.is_file():
        stats["skipped"] += 1
        return
    signature = _file_signature(fp)
    if signature is None:
        stats["skipped"] += 1
        return
    try:
        body = fp.read_text(encoding="utf-8", errors="ignore")[:MAX_SPEC_BYTES]
    except OSError:
        stats["skipped"] += 1
        return
    if not body.strip():
        stats["skipped"] += 1
        return

    store.clear_spec_index(path)
    kind = classify_spec_kind(path)
    title = extract_spec_title(body, fallback=Path(path).name)
    store.index_spec_document(path, kind=kind, title=title, body=body)
    mtime_ns, size = signature
    store.set_spec_index_state(path, mtime_ns, size)
    stats["indexed"] += 1


def update_spec_indexes(
    store: Store,
    repo_root: Path,
    *,
    paths: set[str] | None = None,
    full: bool = False,
) -> dict[str, int]:
    stats = {"indexed": 0, "skipped": 0, "unchanged": 0}
    if full:
        store.clear_all_spec_indexes()

    candidates = discover_spec_files(repo_root)
    if paths is not None:
        candidates = sorted(p for p in candidates if p in paths)

    for path in candidates:
        fp = repo_root / path
        signature = _file_signature(fp)
        if signature is None:
            stats["skipped"] += 1
            continue
        if not full:
            prior = store.get_spec_index_state(path)
            if prior == signature:
                stats["unchanged"] += 1
                continue
        _index_one_spec(store, repo_root, path, stats)

    store.set_meta("spec_index_version", "1", commit=False)
    return stats


def rebuild_spec_indexes(store: Store, repo_root: Path) -> dict[str, int]:
    return update_spec_indexes(store, repo_root, full=True)


def search_spec_context(
    store: Store,
    repo_root: Path,
    query: str,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """BM25 search over indexed specs with snippets for context responses."""
    if not query.strip():
        return []
    hits = store.search_specs_bm25(query, limit=limit)
    results: list[dict[str, object]] = []
    for path, score, kind, title in hits:
        fp = repo_root / path
        snippet = title
        if fp.is_file():
            try:
                body = fp.read_text(encoding="utf-8", errors="ignore")[:MAX_SPEC_BYTES]
                snippet = spec_snippet(body, query)
            except OSError:
                pass
        results.append(
            {
                "path": path,
                "kind": kind,
                "title": title,
                "score": round(score, 3),
                "snippet": snippet,
            }
        )
    return results
