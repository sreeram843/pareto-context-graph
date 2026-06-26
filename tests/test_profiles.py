from __future__ import annotations

from pareto_context_graph.profiles import PROFILES, resolve_profile


def test_profile_resolution():
    huge = resolve_profile("huge")
    assert huge["commits"] == PROFILES["huge"]["commits"]
    assert huge["shards"] == PROFILES["huge"]["shards"]


def test_profile_empty_when_none():
    assert resolve_profile(None) == {}
