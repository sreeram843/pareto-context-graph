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
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def ablation_enabled(signal: str) -> bool:
    """Return True when the given retrieval signal is ablated (zeroed/skipped)."""
    key = f"PCG_ABLATE_{signal.upper()}"
    return os.environ.get(key, "").strip().lower() in _TRUTHY


def active_ablations() -> list[str]:
    return [name for name in ABLATION_SIGNALS if ablation_enabled(name)]
