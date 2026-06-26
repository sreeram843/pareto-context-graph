"""Centralized query intent, file-class, and noise-path taxonomy."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

QUERY_INTENT_TERMS: dict[str, frozenset[str]] = {
    "test": frozenset(
        {
            "test",
            "spec",
            "assert",
            "coverage",
            "mock",
            "fixture",
            "factory",
            "rspec",
            "pytest",
            "jest",
        }
    ),
    "endpoint": frozenset(
        {"endpoint", "route", "routes", "controller", "api", "request", "response"}
    ),
    "schema": frozenset({"schema", "migration", "migrate", "column", "table", "db", "database"}),
    "docs": frozenset({"doc", "docs", "readme", "guide", "documentation"}),
}

TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:spec|test|tests|__tests__)/"
    r"|_(?:spec|test)\."
    r"|\.(?:spec|test)\.",
    re.IGNORECASE,
)

TEST_QUERY_TERMS = frozenset(
    {
        "test",
        "spec",
        "mock",
        "stub",
        "fixture",
        "factory",
        "rspec",
        "jest",
        "pytest",
        "minitest",
        "coverage",
        "assert",
    }
)

NOISE_PATH_PREFIXES = ("docs_src/", "docs/", "scripts/")

CONCEPT_QUERY_HINTS = frozenset(
    {
        "how",
        "what",
        "where",
        "why",
        "authentication",
        "authorize",
        "middleware",
        "openapi",
        "security",
        "dependency",
        "routing",
    }
)


def is_test_file(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def is_noise_path(path: str) -> bool:
    if is_test_file(path):
        return True
    return path.startswith(NOISE_PATH_PREFIXES)


def query_is_test_focused(query: str) -> bool:
    return any(term in TEST_QUERY_TERMS for term in query.lower().split())


def classify_query_intent(query: str) -> str:
    """Classify a query into a lightweight ranking intent."""
    terms = set(query.lower().split())
    best_intent = "default"
    best_score = 0
    for intent, intent_terms in QUERY_INTENT_TERMS.items():
        score = len(terms & intent_terms)
        if score > best_score:
            best_intent = intent
            best_score = score
    return best_intent


def file_class(path: str) -> str:
    """Classify a path into a ranking-oriented file type."""
    lower = path.lower()
    pure = PurePosixPath(lower)

    if (
        any(lower.endswith(suffix) for suffix in (".lock", ".pb.go", ".min.js"))
        or "/dist/" in lower
        or "/build/" in lower
    ):
        return "generated"
    if any(part in {"spec", "test", "tests", "__tests__"} for part in pure.parts) or re.search(
        r"(?:_spec|_test|\.spec|\.test)\.", lower
    ):
        return "test"
    if "/fixtures/" in lower or "/factory/" in lower or "/factories/" in lower:
        return "fixture"
    if "/db/migrate/" in lower or "/migrations/" in lower:
        return "migration"
    if lower.endswith("routes.rb") or "/routes/" in lower or "/templates/" in lower:
        return "route"
    if pure.suffix in {".md", ".rst", ".txt"} or "/docs/" in lower:
        return "doc"
    if (
        "/config/" in lower
        or pure.name in {"dockerfile", "makefile"}
        or pure.suffix
        in {
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".json",
        }
    ):
        return "config"
    return "source"


def looks_like_symbol(query: str) -> bool:
    terms = re.findall(r"[a-zA-Z]\w{2,}", query)
    if not terms:
        return False
    if len(terms) == 1:
        term = terms[0]
        if term[0].isupper() and any(ch.islower() for ch in term[1:]):
            return True
        if "_" in term:
            return True
    return any(term[0].isupper() for term in terms)


def is_concept_query(query: str) -> bool:
    lowered = query.lower()
    if len(query.split()) >= 4:
        return True
    return any(word in lowered for word in CONCEPT_QUERY_HINTS)
