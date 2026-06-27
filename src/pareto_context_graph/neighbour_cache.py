"""In-memory top-neighbour computation (avoids SQL window sorts on large graphs)."""

from __future__ import annotations

import heapq
from collections import defaultdict


def compute_top_neighbours_from_merged(
    merged: dict[tuple[str, str], tuple[float, int]],
    k: int = 50,
) -> dict[str, list[tuple[str, float]]]:
    """Top-*k* neighbours per file from a pre-aggregated co-change map."""
    scores: dict[str, dict[str, float]] = defaultdict(dict)
    for (path_a, path_b), (weight, _ts) in merged.items():
        if weight <= 0:
            continue
        nb_b = scores[path_a]
        nb_b[path_b] = nb_b.get(path_b, 0.0) + weight
        nb_a = scores[path_b]
        nb_a[path_a] = nb_a.get(path_a, 0.0) + weight
    return _rank_top_k(scores, k)


def compute_top_neighbours_from_edges(
    edges: list[tuple[str, str, float]],
    k: int = 50,
) -> dict[str, list[tuple[str, float]]]:
    """Top-*k* neighbours per file from path-level co-change rows."""
    scores: dict[str, dict[str, float]] = defaultdict(dict)
    for path_a, path_b, weight in edges:
        if weight <= 0:
            continue
        nb_b = scores[path_a]
        nb_b[path_b] = nb_b.get(path_b, 0.0) + weight
        nb_a = scores[path_b]
        nb_a[path_a] = nb_a.get(path_a, 0.0) + weight
    return _rank_top_k(scores, k)


def _rank_top_k(
    scores: dict[str, dict[str, float]],
    k: int,
) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for path, neighbours in scores.items():
        if not neighbours:
            continue
        top = heapq.nlargest(k, neighbours.items(), key=lambda item: item[1])
        out[path] = [(nb, float(w)) for nb, w in top]
    return out
