"""Community detection: Leiden (optional) with connected-component fallback."""

from __future__ import annotations

import os

from .profiles import resolve_profile
from .store import Store

_PROFILE_RESOLUTION = {
    "tiny": 1.5,
    "medium": 1.0,
    "large": 0.7,
    "huge": 0.5,
}


def _connected_components(
    store: Store,
    *,
    min_weight: int = 3,
    max_community_size: int = 50,
) -> dict:
    communities = store.get_communities(
        min_weight=min_weight,
        max_community_size=max_community_size,
    )
    return {
        "communities": [
            {"files": files, "size": len(files), "label": f"component_{idx + 1}"}
            for idx, files in enumerate(communities[:10])
        ],
        "total_communities": len(communities),
        "method": "connected_components",
        "modularity": None,
    }


def leiden_communities(
    store: Store,
    *,
    min_weight: int = 3,
    max_community_size: int = 50,
    resolution: float = 1.0,
) -> dict:
    """Detect communities with Leiden when igraph is available."""
    try:
        import igraph as ig
    except ImportError:
        payload = _connected_components(
            store, min_weight=min_weight, max_community_size=max_community_size
        )
        payload["leiden_unavailable"] = True
        return payload

    rows = store.conn.execute(
        """SELECT f1.path, f2.path, c.weight
           FROM co_changes c
           JOIN files f1 ON f1.id = c.file_a
           JOIN files f2 ON f2.id = c.file_b
           WHERE c.weight >= ?""",
        (min_weight,),
    ).fetchall()
    if not rows:
        return _connected_components(
            store, min_weight=min_weight, max_community_size=max_community_size
        )

    paths = sorted({a for a, _b, _w in rows} | {b for _a, b, _w in rows})
    index = {path: idx for idx, path in enumerate(paths)}
    edges = [(index[a], index[b]) for a, b, _w in rows]
    weights = [float(w) for _a, _b, w in rows]

    graph = ig.Graph(n=len(paths), edges=edges, directed=False)
    graph.es["weight"] = weights
    partition = graph.community_leiden(
        weights="weight",
        resolution=resolution,
        n_iterations=2,
    )
    modularity = graph.modularity(partition, weights=weights)

    grouped: dict[int, list[str]] = {}
    for path, membership in zip(paths, partition.membership):
        grouped.setdefault(membership, []).append(path)

    communities = [sorted(files) for files in grouped.values() if len(files) >= 2]
    communities.sort(key=len, reverse=True)

    split: list[list[str]] = []
    for community in communities:
        if len(community) <= max_community_size:
            split.append(community)
            continue
        sub_paths = community
        sub_index = {path: idx for idx, path in enumerate(sub_paths)}
        sub_rows = [
            (sub_index[a], sub_index[b], w) for a, b, w in rows if a in sub_index and b in sub_index
        ]
        if not sub_rows:
            split.append(community[:max_community_size])
            continue
        sub_graph = ig.Graph(
            n=len(sub_paths),
            edges=[(a, b) for a, b, _w in sub_rows],
            directed=False,
        )
        sub_graph.es["weight"] = [w for _a, _b, w in sub_rows]
        sub_partition = sub_graph.community_leiden(
            weights="weight",
            resolution=resolution * 1.2,
            n_iterations=2,
        )
        sub_grouped: dict[int, list[str]] = {}
        for path, membership in zip(sub_paths, sub_partition.membership):
            sub_grouped.setdefault(membership, []).append(path)
        split.extend(sorted(files) for files in sub_grouped.values() if len(files) >= 2)

    split.sort(key=len, reverse=True)
    return {
        "communities": [
            {"files": files, "size": len(files), "label": f"leiden_{idx + 1}"}
            for idx, files in enumerate(split[:10])
        ],
        "total_communities": len(split),
        "method": "leiden",
        "modularity": round(float(modularity), 4),
        "resolution": resolution,
        "_all_communities": split,
    }


def detect_communities(
    store: Store,
    *,
    profile_name: str = "medium",
    min_weight: int = 3,
    max_community_size: int = 50,
    use_leiden: bool = False,
) -> dict:
    profile = resolve_profile(profile_name)
    resolution = float(profile.get("leiden_resolution", _PROFILE_RESOLUTION.get(profile_name, 1.0)))
    max_size = int(profile.get("max_community_size", max_community_size))
    if use_leiden:
        return leiden_communities(
            store,
            min_weight=min_weight,
            max_community_size=max_size,
            resolution=resolution,
        )
    return _connected_components(store, min_weight=min_weight, max_community_size=max_size)


# Additive boost for candidates sharing a Leiden cluster with a seed. Kept on the
# scale of a strong co-change edge (~1-4) so community membership refines ranking
# without burying genuine co-change partners in other clusters. The original flat
# 12.0 dominated the co-change signal entirely. Tunable via env.
COMMUNITY_RANK_BOOST = float(os.environ.get("PCG_COMMUNITY_RANK_BOOST", "3.0"))


def community_membership_map(
    store: Store,
    *,
    profile_name: str = "medium",
    use_leiden: bool = False,
) -> dict[str, int]:
    """Map file path → community id (Leiden when available, else connected components)."""
    payload = detect_communities(
        store,
        profile_name=profile_name,
        use_leiden=use_leiden,
    )
    communities: list[list[str]]
    if payload.get("method") == "leiden":
        communities = payload.get("_all_communities") or [
            item.get("files", []) for item in payload.get("communities", [])
        ]
    else:
        raw = store.get_communities(
            min_weight=3,
            max_community_size=int(resolve_profile(profile_name).get("max_community_size", 50)),
        )
        communities = raw

    mapping: dict[str, int] = {}
    for community_id, files in enumerate(communities):
        for path in files:
            mapping[path] = community_id
    next_id = len(communities)
    for path in store.all_files():
        if path not in mapping:
            mapping[path] = next_id
            next_id += 1
    return mapping


def community_rank_boost(
    path: str,
    seed_files: list[str],
    membership: dict[str, int],
    *,
    boost: float = COMMUNITY_RANK_BOOST,
    seed_only: bool = False,
) -> float:
    """Boost candidates that share a Leiden/component cluster with a seed file.

    Query-driven requests keep the flat cluster boost. Seed-only requests scale
    community boost by directory proximity and lift same-directory singleton
    siblings that weak co-change edges isolate from the seed cluster.
    """
    if not seed_files or not membership:
        return 0.0
    path_community = membership.get(path)
    if path_community is None:
        return 0.0
    seed_communities = {membership[seed] for seed in seed_files if seed in membership}
    cluster_match = path_community in seed_communities

    if not seed_only:
        if cluster_match:
            return boost
        return 0.0

    from .context_ranking import locality_multiplier

    loc_ratio = locality_multiplier(path, seed_files) / 3.0
    if loc_ratio <= 0:
        return 0.0

    if cluster_match:
        return boost * loc_ratio

    compat_seed = any("_compat/" in seed.replace("\\", "/") for seed in seed_files)
    if compat_seed and loc_ratio >= 1.0 and path_community not in seed_communities:
        return boost * loc_ratio
    return 0.0
