"""Selective HTTP route edges (FastAPI/Flask-style) for structural graph (#19)."""

from __future__ import annotations

import re
from pathlib import Path

from .blast import extract_imports, resolve_import_to_file

_ROUTE_DECORATOR_RE = re.compile(
    r"@(?:app|router|\w+)\.(get|post|put|delete|patch|head|options|route)\s*\(",
    re.IGNORECASE,
)
_INCLUDE_ROUTER_RE = re.compile(
    r"\b(?:app|router)\.(?:include_router|mount)\s*\(\s*(\w+)",
    re.IGNORECASE,
)
_FLASK_ROUTE_RE = re.compile(
    r"@(?:app|bp|\w+)\.(?:route|get|post|put|delete)\s*\(",
    re.IGNORECASE,
)


def _has_route_markers(content: str) -> bool:
    return bool(
        _ROUTE_DECORATOR_RE.search(content)
        or _FLASK_ROUTE_RE.search(content)
        or _INCLUDE_ROUTER_RE.search(content)
    )


def extract_route_edges(
    file_path: Path,
    repo_path: str,
    all_files: set[str],
    *,
    content: str | None = None,
) -> list[dict]:
    """Return route-kind edges from a routes/controllers module."""
    try:
        body = content if content is not None else file_path.read_text(errors="ignore")[:50_000]
    except OSError:
        return []

    if not _has_route_markers(body):
        return []

    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(dst: str) -> None:
        if not dst or dst == repo_path:
            return
        key = (repo_path, dst)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            {
                "src_path": repo_path,
                "dst_path": dst,
                "kind": "route",
                "confidence": "INFERRED",
            }
        )

    for ref in extract_imports(file_path):
        resolved = resolve_import_to_file(ref, all_files, from_path=repo_path)
        if resolved:
            add(resolved)

    for match in _INCLUDE_ROUTER_RE.finditer(body):
        symbol = match.group(1)
        for ref in extract_imports(file_path):
            if ref.endswith(f".{symbol}") or ref.split(".")[-1] == symbol:
                resolved = resolve_import_to_file(ref, all_files, from_path=repo_path)
                if resolved:
                    add(resolved)

    return edges
