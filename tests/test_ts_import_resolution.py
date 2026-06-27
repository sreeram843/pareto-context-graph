"""Tests for TypeScript/JavaScript import resolution."""

from __future__ import annotations

from pareto_context_graph.blast import resolve_import_to_file


def test_resolve_relative_ts_import():
    files = {
        "src/components/Button.tsx",
        "src/utils/format.ts",
        "src/utils/index.ts",
    }
    resolved = resolve_import_to_file(
        "../utils/format", files, from_path="src/components/Button.tsx"
    )
    assert resolved == "src/utils/format.ts"


def test_resolve_alias_import():
    files = {
        "src/lib/auth.ts",
        "src/app/page.tsx",
    }
    resolved = resolve_import_to_file("@/lib/auth", files, from_path="src/app/page.tsx")
    assert resolved == "src/lib/auth.ts"


def test_resolve_scoped_package_import():
    files = {
        "node_modules/@acme/shared/index.ts",
        "src/main.ts",
    }
    resolved = resolve_import_to_file("@acme/shared", files, from_path="src/main.ts")
    assert resolved == "node_modules/@acme/shared/index.ts"
