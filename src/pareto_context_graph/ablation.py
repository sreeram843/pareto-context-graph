"""Per-signal ablation flags for eval science (PCG_ABLATE_<SIGNAL>=1)."""

from __future__ import annotations

import os

ABLATION_SIGNALS = (
    "bm25",
    "symbol",
    "embed",
    "co_change",
    "learned",
    "semantic",
    "import",
    "prf",
    "mmr_top5",
    "openapi_downweight",
    "hubfloor",
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def ablation_enabled(signal: str) -> bool:
    """Return True when the given retrieval signal is ablated (zeroed/skipped)."""
    key = f"PCG_ABLATE_{signal.upper()}"
    return os.environ.get(key, "").strip().lower() in _TRUTHY


def active_ablations() -> list[str]:
    return [name for name in ABLATION_SIGNALS if ablation_enabled(name)]


# Feature opt-out env vars for ablation studies (not PCG_ABLATE_*).
FEATURE_ABLATION_ENV: dict[str, tuple[str, str]] = {
    "community_rank": ("PCG_FEATURE_COMMUNITY_RANK", "0"),
}
