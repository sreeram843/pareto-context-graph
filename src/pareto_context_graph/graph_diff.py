"""Git-diff impact and graph coupling changes (CRG §6 / GitNexus detect_changes)."""

from __future__ import annotations

from pathlib import Path

from .blast import blast_radius, filter_existing
from .community import detect_communities
from .features import feature_enabled
from .graph import _run_git, get_changed_files
from .profiles import autodetect_profile
from .store import Store


def _git_head(repo_root: Path) -> str:
    try:
        return _run_git(["rev-parse", "HEAD"], repo_root).strip()
    except RuntimeError:
        return ""


def detect_changes(
    repo_root: Path,
    *,
    base: str = "main",
    min_weight: int = 2,
    max_depth: int = 2,
    max_results: int = 100,
    top_hubs: int = 5,
) -> dict:
    """Map a git diff to blast radius, hub disruption, and index staleness."""
    changed = get_changed_files(repo_root, base=base)
    store = Store(repo_root)
    try:
        indexed_head = store.get_meta("last_commit_hash") or ""
        current_head = _git_head(repo_root)
        stale_index = bool(indexed_head and current_head and indexed_head != current_head)

        if not changed:
            return {
                "base": base,
                "changed": [],
                "affected": [],
                "blast_count": 0,
                "community_labels_touched": [],
                "emerging_hubs": [],
                "stale_index": stale_index,
                "indexed_head": indexed_head or None,
                "current_head": current_head or None,
                "hint": "No diff vs base; run update if stale_index is true.",
            }

        results = blast_radius(
            store,
            changed,
            min_weight=min_weight,
            max_depth=max_depth,
            max_results=max_results,
            use_structural=feature_enabled("STRUCTURAL_EDGES"),
        )
        changed_set = set(changed)
        affected_rows = [r for r in results if r["path"] not in changed_set]
        existing = filter_existing(repo_root, [r["path"] for r in affected_rows])
        affected_rows = [r for r in affected_rows if r["path"] in existing]

        stats = store.graph_stats()
        p95_degree = int(stats.get("p95_degree") or 0)
        hub_threshold = max(p95_degree, 10)
        emerging = [
            {
                "path": r["path"],
                "depth": r.get("depth"),
                "weight": r.get("weight"),
                "degree": next(
                    (h["degree"] for h in stats.get("top_hubs", []) if h["path"] == r["path"]),
                    None,
                ),
            }
            for r in affected_rows
            if any(
                h["path"] == r["path"] and h["degree"] >= hub_threshold
                for h in stats.get("top_hubs", [])
            )
        ][:top_hubs]

        profile = autodetect_profile(repo_root) or "medium"
        comm = detect_communities(
            store,
            profile_name=profile,
            use_leiden=feature_enabled("LEIDEN"),
        )
        touched_paths = changed_set | {r["path"] for r in affected_rows}
        labels: list[str] = []
        for block in comm.get("communities", []):
            files = block.get("files") or []
            if any(f in touched_paths for f in files):
                labels.append(str(block.get("label", "unknown")))

        return {
            "base": base,
            "changed": changed,
            "affected": [r["path"] for r in affected_rows],
            "blast_details": affected_rows[:25],
            "blast_count": len(affected_rows),
            "community_labels_touched": labels,
            "emerging_hubs": emerging,
            "stale_index": stale_index,
            "indexed_head": indexed_head or None,
            "current_head": current_head or None,
            "hint": (
                "Run pareto-context-graph update when stale_index is true after merging."
                if stale_index
                else "Review affected files before merge; escalate with context tier 2–3 on hubs."
            ),
        }
    finally:
        store.close()
