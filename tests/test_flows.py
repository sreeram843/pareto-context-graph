"""Flow ground-truth schema validation + call-path self-verification (Phase 1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pareto_context_graph.flows import load_flows, verify_call_path

BENCH = Path(__file__).resolve().parents[1] / "bench"


def test_flows_load_and_have_required_fields():
    flows = load_flows()
    assert flows, "expected at least one flow"
    for flow in flows:
        assert flow.flow_id and flow.repo_key and flow.question
        assert flow.call_path, f"{flow.flow_id} has no call path"
        assert flow.must_hit_symbols, f"{flow.flow_id} has no must_hit_symbols"
        # Every must-hit symbol should appear somewhere in the call path.
        path_syms = {s.symbol.split(".")[-1] for s in flow.call_path}
        for sym in flow.must_hit_symbols:
            assert sym.split(".")[-1] in path_syms, f"{flow.flow_id}: {sym} not in call path"


@pytest.mark.parametrize("flow", load_flows(), ids=lambda f: f.flow_id)
def test_call_path_symbols_exist_in_repo(flow):
    """Each cited file:line must still resolve in the cloned bench repo (anti-rot)."""
    repo_root = BENCH / flow.repo_key
    if not (repo_root / ".git").is_dir():
        pytest.skip(f"bench/{flow.repo_key} not cloned")
    problems = verify_call_path(flow, repo_root)
    assert not problems, "\n".join(problems)
