"""Optional tree-sitter symbol extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from pareto_context_graph.symbols import _extract_treesitter_records, _treesitter_available


@pytest.mark.skipif(not _treesitter_available(), reason="tree-sitter extra not installed")
def test_treesitter_extracts_python_function(tmp_path: Path):
    fp = tmp_path / "mod.py"
    fp.write_text(
        "class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    return 1\n"
    )
    records = _extract_treesitter_records(fp)
    names = {r["symbol"] for r in records}
    assert "Foo" in names
    assert "bar" in names
    assert "baz" in names
