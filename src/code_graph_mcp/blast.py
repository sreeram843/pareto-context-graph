"""Blast-radius calculation from the co-change graph."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .store import Store

# Lightweight import detection patterns (no Tree-sitter needed)
_IMPORT_PATTERNS = [
    re.compile(r'''^\s*(?:from|import)\s+['".]?(\S+)''', re.MULTILINE),  # Python
    re.compile(r'''^\s*(?:require|require_relative)\s+['"]([^'"]+)''', re.MULTILINE),  # Ruby
    re.compile(r'''^\s*(?:import|export)\s.*?from\s+['"]([^'"]+)''', re.MULTILINE),  # JS/TS
    re.compile(r'''^\s*(?:const|let|var)\s.*?=\s*require\s*\(\s*['"]([^'"]+)''', re.MULTILINE),  # CJS
    re.compile(r'''^\s*#include\s+["<]([^">]+)''', re.MULTILINE),  # C/C++
    re.compile(r'''^\s*use\s+([^\s;]+)''', re.MULTILINE),  # Rust/Perl
    re.compile(r'''^\s*package\s+(\S+)|^\s*import\s+"([^"]+)"''', re.MULTILINE),  # Go
]

_QUERY_INTENT_TERMS = {
    "test": {"test", "spec", "assert", "coverage", "mock", "fixture", "factory", "rspec", "pytest", "jest"},
    "endpoint": {"endpoint", "route", "routes", "controller", "api", "request", "response"},
    "schema": {"schema", "migration", "migrate", "column", "table", "db", "database"},
    "docs": {"doc", "docs", "readme", "guide", "documentation"},
}

# dbt Jinja reference patterns
_DBT_REF_RE = re.compile(r"""\{\{\s*ref\s*\(\s*['"]([^'"]+)['"]\s*\)\s*\}\}""")
_DBT_SOURCE_RE = re.compile(r"""\{\{\s*source\s*\(\s*['"][^'"]+['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*\}\}""")


def extract_imports(file_path: Path) -> list[str]:
    """Extract import references from a file using regex (language-agnostic best-effort)."""
    try:
        content = file_path.read_text(errors="ignore")[:10000]  # First 10KB only
    except (OSError, UnicodeDecodeError):
        return []
    imports = []
    for pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(content):
            # Get first non-None group
            ref = next((g for g in match.groups() if g), None)
            if ref:
                imports.append(ref)
    return imports


def extract_dbt_refs(file_path: Path) -> list[str]:
    """Extract dbt {{ ref('model') }} and {{ source('src', 'table') }} model names."""
    try:
        content = file_path.read_text(errors="ignore")[:50000]
    except (OSError, UnicodeDecodeError):
        return []
    refs: list[str] = []
    for m in _DBT_REF_RE.finditer(content):
        refs.append(m.group(1))
    for m in _DBT_SOURCE_RE.finditer(content):
        refs.append(m.group(1))
    return refs


def resolve_dbt_ref_to_file(model_name: str, all_files: set[str]) -> str | None:
    """Resolve a dbt model name to its SQL file by matching the file stem."""
    lower_name = model_name.lower()
    # Exact stem match first
    for f in all_files:
        p = PurePosixPath(f)
        if p.suffix == ".sql" and p.stem.lower() == lower_name:
            return f
    # Double-underscore suffix convention: ods_schema__model_name -> match model_name part
    for f in all_files:
        p = PurePosixPath(f)
        if p.suffix == ".sql" and p.stem.lower().endswith("__" + lower_name):
            return f
    return None


def _snake_case(value: str) -> str:
    step1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    step2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step1)
    return step2.replace("-", "_").lower()


def resolve_import_to_file(ref: str, all_files: set[str], from_path: str | None = None) -> str | None:
    """Try to resolve an import reference to an actual file in the repo."""
    ref = ref.strip()
    normalized = ref.replace("@/", "").replace("::", "/").replace(".", "/").lstrip("/")

    if "::" in ref:
        normalized = "/".join(_snake_case(part) for part in ref.split("::") if part)

    # Try common patterns
    candidates = [
        normalized,
        normalized + ".py",
        normalized + ".rb",
        normalized + ".ts",
        normalized + ".js",
        normalized + ".tsx",
        normalized + ".jsx",
        normalized + "/index.ts",
        normalized + "/index.js",
    ]

    if from_path and ref.startswith("."):
        base_parent = PurePosixPath(from_path).parent
        relative = PurePosixPath(base_parent, ref).as_posix().replace("./", "")
        relative = str(PurePosixPath(relative))
        candidates = [
            relative,
            relative + ".py",
            relative + ".rb",
            relative + ".ts",
            relative + ".js",
            relative + ".tsx",
            relative + ".jsx",
        ] + candidates

    if "::" in ref:
        candidates.extend([
            f"app/{normalized}.rb",
            f"lib/{normalized}.rb",
            f"app/models/{normalized}.rb",
            f"app/services/{normalized}.rb",
            f"app/controllers/{normalized}.rb",
        ])

    for c in candidates:
        if c in all_files:
            return c
        # Check suffix match (e.g., "auth_service" matches "app/services/auth_service.rb")
        for f in all_files:
            if f.endswith("/" + c) or f.endswith("/" + normalized + ".rb") or f.endswith("/" + normalized + ".py"):
                return f
    return None


def classify_query_intent(query: str) -> str:
    """Classify a query into a lightweight ranking intent."""
    terms = set(query.lower().split())
    best_intent = "default"
    best_score = 0
    for intent, intent_terms in _QUERY_INTENT_TERMS.items():
        score = len(terms & intent_terms)
        if score > best_score:
            best_intent = intent
            best_score = score
    return best_intent


def file_class(path: str) -> str:
    """Classify a path into a ranking-oriented file type."""
    lower = path.lower()
    pure = PurePosixPath(lower)

    if any(lower.endswith(suffix) for suffix in (".lock", ".pb.go", ".min.js")) or "/dist/" in lower or "/build/" in lower:
        return "generated"
    if any(part in {"spec", "test", "tests", "__tests__"} for part in pure.parts) or re.search(r"(?:_spec|_test|\.spec|\.test)\.", lower):
        return "test"
    if "/fixtures/" in lower or "/factory/" in lower or "/factories/" in lower:
        return "fixture"
    if "/db/migrate/" in lower or "/migrations/" in lower:
        return "migration"
    if lower.endswith("routes.rb") or "/routes/" in lower or "/templates/" in lower:
        return "route"
    if pure.suffix in {".md", ".rst", ".txt"} or "/docs/" in lower:
        return "doc"
    if "/config/" in lower or pure.name in {"dockerfile", "makefile"} or pure.suffix in {".yaml", ".yml", ".toml", ".ini", ".json"}:
        return "config"
    return "source"


def find_naming_pairs(path: str, all_files: set[str]) -> list[str]:
    """Find files related by naming convention (test, spec, impl pairs)."""
    stem = PurePosixPath(path).stem
    parent = str(PurePosixPath(path).parent)
    pairs = []

    # foo.rb ↔ foo_spec.rb, foo_test.rb, test_foo.rb
    test_variants = [
        f"{stem}_spec", f"{stem}_test", f"test_{stem}",
        f"{stem}.spec", f"{stem}.test",
    ]
    # foo_controller ↔ foo_service, foo_helper
    base = stem.replace("_controller", "").replace("_service", "").replace("_helper", "")
    role_variants = [
        f"{base}_controller", f"{base}_service", f"{base}_helper",
        f"{base}_model", f"{base}_serializer", f"{base}_decorator",
    ]

    for f in all_files:
        f_stem = PurePosixPath(f).stem
        if f == path:
            continue
        if f_stem in test_variants or f_stem in role_variants:
            pairs.append(f)
    return pairs[:10]  # Cap to avoid explosion


def find_directory_siblings(path: str, all_files: set[str], limit: int = 10) -> list[str]:
    """Return nearby files from the same directory for sparse-graph fallback."""
    parent = PurePosixPath(path).parent
    siblings: list[str] = []
    for candidate in sorted(all_files):
        if candidate == path:
            continue
        if PurePosixPath(candidate).parent == parent:
            siblings.append(candidate)
        if len(siblings) >= limit:
            break
    return siblings


def get_file_summary(file_path: Path) -> str:
    """Extract a 1-line summary from a file (first docstring, class, or comment)."""
    try:
        lines = file_path.read_text(errors="ignore").splitlines()[:30]
    except (OSError, UnicodeDecodeError):
        return ""
    for line in lines:
        stripped = line.strip()
        # Skip empty, shebangs, encoding declarations
        if not stripped or stripped.startswith("#!") or "coding:" in stripped:
            continue
        # Python/Ruby docstrings and comments
        if stripped.startswith(('"""', "'''", "#", "//", "/*", "*")):
            summary = stripped.strip("\"'#/*\\ ").strip()
            if len(summary) > 10:
                return summary[:120]
        # Class/module/function definitions
        if any(stripped.startswith(kw) for kw in ("class ", "module ", "def ", "function ", "export ", "pub ")):
            return stripped[:120]
    return ""


def blast_radius(
    store: Store,
    changed_files: list[str],
    *,
    min_weight: int = 2,
    max_depth: int = 2,
    max_results: int = 100,
    use_cache: bool = False,
) -> list[dict]:
    """Compute the blast radius for a set of changed files.

    Uses BFS over the co-change graph: files that frequently change together
    are likely coupled.  Depth limits prevent runaway expansion.

    Args:
        store: The co-change graph store.
        changed_files: Files that changed (from git diff).
        min_weight: Minimum co-change count to consider a link significant.
        max_depth: How many hops from changed files to explore.
        max_results: Cap on total files returned.
        use_cache: When True, use the pre-built top_neighbours cache table
            instead of scanning co_changes directly (faster for large graphs).

    Returns:
        List of dicts with path, depth, weight (sorted by weight desc).
    """
    visited: dict[str, dict] = {}
    # Seed with changed files themselves
    for f in changed_files:
        visited[f] = {"path": f, "depth": 0, "weight": 0, "source": "changed"}

    frontier = list(changed_files)

    for depth in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for src in frontier:
            if use_cache:
                raw = store.top_neighbours(src, limit=50)
                neighbours = [(p, w) for p, w in raw if w >= min_weight]
                if not neighbours:
                    neighbours = store.neighbours(src, min_weight=min_weight)
            else:
                neighbours = store.neighbours(src, min_weight=min_weight)
            for neighbour, weight in neighbours:
                if neighbour in visited:
                    # Update weight if this path is stronger
                    if weight > visited[neighbour]["weight"]:
                        visited[neighbour]["weight"] = weight
                    continue
                visited[neighbour] = {
                    "path": neighbour,
                    "depth": depth,
                    "weight": weight,
                    "source": src,
                }
                next_frontier.append(neighbour)
        frontier = next_frontier

    # Sort: changed files first, then by weight descending
    results = sorted(
        visited.values(),
        key=lambda x: (-1 if x["depth"] == 0 else 0, -x["weight"]),
    )
    return results[:max_results]


def blast_radius_paths(
    repo_root: Path,
    changed_files: list[str],
    *,
    min_weight: int = 2,
    max_depth: int = 2,
) -> list[str]:
    """Convenience: return just the file paths in the blast radius."""
    store = Store(repo_root)
    try:
        results = blast_radius(
            store, changed_files, min_weight=min_weight, max_depth=max_depth
        )
        return [r["path"] for r in results]
    finally:
        store.close()


def filter_existing(repo_root: Path, paths: list[str]) -> list[str]:
    """Keep only paths that still exist on disk."""
    return [p for p in paths if (repo_root / p).is_file()]


def _dir_distance(path_a: str, path_b: str) -> int:
    """Number of divergent directory components between two paths."""
    parts_a = PurePosixPath(path_a).parent.parts
    parts_b = PurePosixPath(path_b).parent.parts
    # Find common prefix length
    common = 0
    for a, b in zip(parts_a, parts_b):
        if a == b:
            common += 1
        else:
            break
    return (len(parts_a) - common) + (len(parts_b) - common)


def surprise_score(results: list[dict], changed_files: list[str]) -> list[dict]:
    """Add surprise scores to blast radius results.

    Surprise = cross-directory coupling. Files that co-change across module
    boundaries are more likely to be missed in reviews (high surprise).
    """
    for r in results:
        if r["depth"] == 0:
            r["surprise"] = 0
            continue
        # Max distance from any changed file
        max_dist = max(
            _dir_distance(r["path"], cf) for cf in changed_files
        ) if changed_files else 0
        # Surprise = distance * weight (cross-module AND frequent = most surprising)
        r["surprise"] = max_dist * r["weight"]
    return results
