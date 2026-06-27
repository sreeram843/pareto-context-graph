"""Feature flag defaults and opt-out."""

from __future__ import annotations

from pareto_context_graph.features import DEFAULT_ON_FEATURES, feature_enabled, request_flag


def test_default_on_features():
    assert DEFAULT_ON_FEATURES == {
        "QUERY_FIRST",
        "DIAGNOSTICS",
        "STRUCTURAL_EDGES",
        "LEIDEN",
        "SESSION_MEMORY",
        "COMMUNITY_RANK",
        "PRF_COCHANGE",
        "TREESITTER",
    }
    for name in DEFAULT_ON_FEATURES:
        assert feature_enabled(name) is True


def test_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("PCG_FEATURE_QUERY_FIRST", "0")
    assert feature_enabled("QUERY_FIRST") is False
    monkeypatch.setenv("PCG_FEATURE_QUERY_FIRST", "off")
    assert feature_enabled("QUERY_FIRST") is False


def test_explicit_opt_in_unknown_feature(monkeypatch):
    monkeypatch.delenv("PCG_FEATURE_EXPORT", raising=False)
    assert feature_enabled("EXPORT") is False
    monkeypatch.setenv("PCG_FEATURE_EXPORT", "1")
    assert feature_enabled("EXPORT") is True


def test_request_flag_argument_override():
    assert request_flag({"query_first": False}, "query_first", "QUERY_FIRST") is False
    assert request_flag({"query_first": True}, "query_first", "QUERY_FIRST") is True
    assert request_flag({}, "query_first", "QUERY_FIRST") is True
