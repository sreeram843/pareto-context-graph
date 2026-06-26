"""Automatic co-change edge decay for long-lived graphs."""

from __future__ import annotations

import os
import time

from .store import Store

DEFAULT_DECAY_INTERVAL_SECONDS = int(
    os.environ.get("PCG_EDGE_DECAY_INTERVAL_SECONDS", str(24 * 3600))
)
DEFAULT_HALF_LIFE_DAYS = float(os.environ.get("PCG_EDGE_DECAY_HALF_LIFE_DAYS", "180"))
DEFAULT_PRUNE_BELOW = float(os.environ.get("PCG_EDGE_DECAY_PRUNE_BELOW", "0.5"))


def maybe_decay_cochange_edges(store: Store) -> dict[str, int | bool | float | str]:
    """Apply exponential decay when the interval elapsed since the last sweep."""
    if os.environ.get("PCG_EDGE_DECAY", "").lower() in {"0", "false", "no"}:
        return {"skipped": True, "reason": "disabled"}

    now = int(time.time())
    last_raw = store.get_meta("last_automatic_decay_ts")
    if last_raw:
        elapsed = now - int(last_raw)
        if elapsed < DEFAULT_DECAY_INTERVAL_SECONDS:
            return {"skipped": True, "seconds_until_next": DEFAULT_DECAY_INTERVAL_SECONDS - elapsed}

    deleted = store.apply_decay(
        half_life_days=DEFAULT_HALF_LIFE_DAYS,
        prune_below=DEFAULT_PRUNE_BELOW,
    )
    store.set_meta("last_automatic_decay_ts", str(now))
    return {
        "applied": True,
        "half_life_days": DEFAULT_HALF_LIFE_DAYS,
        "prune_below": DEFAULT_PRUNE_BELOW,
        "edges_pruned": deleted,
    }
