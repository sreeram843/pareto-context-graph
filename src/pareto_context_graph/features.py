"""Feature flags (default-on for shipped retrieval features; env opt-out)."""

from __future__ import annotations

import os

# Enabled by default after Phase 3–4 eval sign-off. Set PCG_FEATURE_<NAME>=0 to disable.
DEFAULT_ON_FEATURES = frozenset(
    {
        "QUERY_FIRST",
        "DIAGNOSTICS",
        "STRUCTURAL_EDGES",
        "LEIDEN",
        "SESSION_MEMORY",
        "COMMUNITY_RANK",
        "PRF_COCHANGE",
        "TREESITTER",
    }
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def feature_enabled(name: str) -> bool:
    """Return True when the feature is on (default-on set or explicit env)."""
    key = f"PCG_FEATURE_{name.upper()}"
    raw = os.environ.get(key)
    if raw is not None and raw.strip() != "":
        val = raw.strip().lower()
        if val in _FALSY:
            return False
        if val in _TRUTHY:
            return True
        return False
    return name.upper() in DEFAULT_ON_FEATURES


def request_flag(arguments: dict, arg_name: str, feature_name: str) -> bool:
    """Request arg overrides env feature flag when explicitly set."""
    if arg_name in arguments:
        return bool(arguments[arg_name])
    return feature_enabled(feature_name)
