"""Tests for SWE-Pruner-style tier-1 summary prune (Phase 11.4)."""

from pareto_context_graph.summary_prune import apply_summary_prune, tier1_entry_matches_query


def test_tier1_entry_matches_query_by_summary():
    terms = {"template", "jinja"}
    entry = {
        "path": "docs/en/advanced/templates.md",
        "summary": "Jinja2 templates and TemplateResponse usage",
    }
    assert tier1_entry_matches_query(entry, query_terms_set=terms, seed_files=set()) is True


def test_tier1_entry_matches_query_seed_always_kept():
    entry = {"path": "unrelated.py", "summary": "nothing relevant"}
    assert (
        tier1_entry_matches_query(
            entry,
            query_terms_set={"authenticate"},
            seed_files={"unrelated.py"},
        )
        is True
    )


def test_tier1_entry_matches_query_signal_kept():
    entry = {
        "path": "pkg/foo.py",
        "summary": "generic module",
        "signal": "semantic",
    }
    assert (
        tier1_entry_matches_query(
            entry,
            query_terms_set={"oauth"},
            seed_files=set(),
        )
        is True
    )


def test_apply_summary_prune_drops_mismatched_rows():
    files = [
        {
            "path": "fastapi/routing.py",
            "summary": "APIRouter and route registration",
            "tokens_actual": 40,
        },
        {
            "path": "docs/logo.md",
            "summary": "Project branding assets",
            "tokens_actual": 30,
        },
        {
            "path": "fastapi/dependencies/utils.py",
            "summary": "Depends() and dependency injection helpers",
            "tokens_actual": 35,
        },
    ]
    kept, meta = apply_summary_prune(
        files,
        query="APIRouter routing Depends",
        tier=1,
        seed_files=[],
        min_keep=2,
        protect_top=1,
    )
    paths = [entry["path"] for entry in kept]
    assert "docs/logo.md" not in paths
    assert meta["dropped_count"] == 1
    assert len(kept) >= 2


def test_apply_summary_prune_skips_non_tier1():
    files = [{"path": "a.py", "summary": "x", "content": "big"}]
    kept, meta = apply_summary_prune(files, query="missing", tier=3)
    assert kept == files
    assert meta == {}


def test_apply_summary_prune_respects_min_keep():
    files = [
        {"path": f"noise/{idx}.py", "summary": "unrelated boilerplate", "tokens_actual": 10}
        for idx in range(6)
    ]
    kept, meta = apply_summary_prune(
        files,
        query="zebra authentication",
        tier=1,
        min_keep=4,
        protect_top=0,
    )
    assert len(kept) == 4
    assert meta["dropped_count"] == 2


def test_apply_summary_prune_protects_top_ranked_rows():
    files = [
        {"path": "fastapi/openapi/utils.py", "summary": "generic utilities", "tokens_actual": 40},
        {"path": "fastapi/openapi/models.py", "summary": "schema models", "tokens_actual": 35},
        {"path": "docs/logo.md", "summary": "branding only", "tokens_actual": 20},
        {"path": "noise/extra.py", "summary": "unrelated", "tokens_actual": 15},
    ]
    kept, meta = apply_summary_prune(
        files,
        query="OpenAPI schema generation",
        tier=1,
        protect_top=2,
        min_keep=2,
    )
    paths = [entry["path"] for entry in kept]
    assert paths[:2] == ["fastapi/openapi/utils.py", "fastapi/openapi/models.py"]
    assert meta["protected_top"] == 2
    assert "docs/logo.md" not in paths[2:]
