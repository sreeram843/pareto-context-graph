from __future__ import annotations

import random
from collections import defaultdict

from .store import Store


def random_walk_with_restart(
    store: Store,
    seeds: list[str],
    *,
    walks: int = 200,
    length: int = 6,
    restart: float = 0.15,
) -> dict[str, float]:
    """Approximate Personalized PageRank using random walks with restart."""
    if not seeds:
        return {}

    rng = random.Random(0)
    scores: dict[str, float] = defaultdict(float)

    for seed in seeds:
        for _ in range(walks):
            current = seed
            for _step in range(length):
                scores[current] += 1.0
                if rng.random() < restart:
                    current = seed
                    continue

                neigh = store.top_neighbours(current, limit=50)
                if not neigh:
                    break

                total_weight = sum(max(weight, 0.0) for _path, weight in neigh)
                if total_weight <= 0:
                    current = neigh[rng.randrange(len(neigh))][0]
                    continue

                pick = rng.random() * total_weight
                cursor = 0.0
                for candidate_path, weight in neigh:
                    cursor += max(weight, 0.0)
                    if cursor >= pick:
                        current = candidate_path
                        break

    total = sum(scores.values()) or 1.0
    return {path: value / total for path, value in scores.items()}
