"""Structural edges (calls, inherits, tests) extracted at build time."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .blast import extract_imports, resolve_import_to_file
from .route_edges import extract_route_edges
from .symbols import CODE_EXTENSIONS

_INHERIT_PATTERNS = [
    re.compile(r"^\s*class\s+(\w+)\s*\(\s*(\w+)", re.MULTILINE),
    re.compile(r"^\s*class\s+(\w+)\s+<\s*(\w+)", re.MULTILINE),
    re.compile(r"^\s*class\s+(\w+)\s+extends\s+(\w+)", re.MULTILINE),
]

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|spec|__tests__)/|(^|/)test_|_test\.|_spec\.|\.spec\.|\.test\.",
    re.IGNORECASE,
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path.replace("\\", "/")))


def infer_test_target(path: str, all_files: set[str]) -> str | None:
    """Map a test file path to a likely implementation file."""
    pure = PurePosixPath(path)
    stem = pure.stem.lower()
    for suffix in ("_test", "_spec", ".test", ".spec"):
        if stem.endswith(suffix.replace(".", "_")):
            stem = stem[: -len(suffix.replace(".", "_"))]
            break
    if stem.startswith("test_"):
        stem = stem[5:]

    candidates = [
        f"{pure.parent}/{stem}{pure.suffix}",
        f"{pure.parent.parent}/{stem}{pure.suffix}",
        f"src/{stem}{pure.suffix}",
    ]
    for candidate in candidates:
        normalized = candidate.replace("\\", "/")
        if normalized in all_files:
            return normalized

    for file_path in all_files:
        if PurePosixPath(file_path).stem.lower() == stem and not is_test_path(file_path):
            return file_path
    return None


def extract_structural_edges(
    file_path: Path,
    repo_path: str,
    all_files: set[str],
    *,
    max_bytes: int = 50_000,
) -> list[dict]:
    """Return structural edges originating from *repo_path*."""
    if file_path.suffix.lower() not in CODE_EXTENSIONS:
        return []

    edges: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(dst: str, kind: str, confidence: str = "INFERRED") -> None:
        if not dst or dst == repo_path:
            return
        key = (repo_path, dst, kind)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            {
                "src_path": repo_path,
                "dst_path": dst,
                "kind": kind,
                "confidence": confidence,
            }
        )

    if is_test_path(repo_path):
        target = infer_test_target(repo_path, all_files)
        if target:
            add(target, "tests", "INFERRED")

    for ref in extract_imports(file_path):
        resolved = resolve_import_to_file(ref, all_files, from_path=repo_path)
        if resolved:
            add(resolved, "calls", "INFERRED")

    try:
        content = file_path.read_text(errors="ignore")[:max_bytes]
    except OSError:
        content = ""

    for pattern in _INHERIT_PATTERNS:
        for match in pattern.finditer(content):
            parent = match.group(2)
            if parent in {"object", "Exception", "Base", "BaseModel", "ABC"}:
                continue
            for file_path_candidate in all_files:
                if PurePosixPath(file_path_candidate).stem == parent:
                    add(file_path_candidate, "inherits", "EXTRACTED")
                    break

    for edge in extract_route_edges(file_path, repo_path, all_files, content=content):
        add(edge["dst_path"], edge["kind"], edge.get("confidence", "INFERRED"))

    return edges
