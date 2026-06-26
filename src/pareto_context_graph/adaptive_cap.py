"""Adaptive stage-1 candidate cap from query complexity (SWE-Pruner / ACON P0)."""

from __future__ import annotations

import re

from .deadlines import MEGA_HUB_STAGE1_CAP

SHORT_QUERY_CAP = 25
MEDIUM_QUERY_CAP = 50
LONG_QUERY_CAP = 75

_TERM_RE = re.compile(r"\w+")


def query_term_count(query: str) -> int:
    return len(_TERM_RE.findall((query or "").lower()))


def adaptive_stage1_cap(
    query: str,
    *,
    profile_cap: int,
    high_fanout: bool = False,
) -> int:
    """Pick a stage-1 cap from query shape; never exceeds ``profile_cap``.

    - Hub / high-fanout seeds: use the existing mega-hub cap (75).
    - Empty query (seed-only): narrow expansion (25).
    - Short queries (≤2 terms, <48 chars): 25.
    - Long / multi-term (≥5 terms or ≥80 chars): 75.
    - Otherwise: 50.
    """
    if high_fanout:
        return min(profile_cap, MEGA_HUB_STAGE1_CAP)

    q = (query or "").strip()
    if not q:
        return min(profile_cap, SHORT_QUERY_CAP)

    terms = query_term_count(q)
    if terms <= 2 and len(q) < 48:
        cap = SHORT_QUERY_CAP
    elif terms >= 5 or len(q) >= 80:
        cap = LONG_QUERY_CAP
    else:
        cap = MEDIUM_QUERY_CAP

    return min(profile_cap, cap)
