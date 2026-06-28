"""Learned re-rankers from feedback events (logistic + optional LambdaMART)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .store import DB_DIR

FEATURE_KEYS = (
    "co_change",
    "bm25",
    "symbol",
    "embed",
    "locality",
    "hub_penalty",
    "learned_boost",
    "rank_score",
    # Phase 4.1 experiment (file_class_prior as a learned feature) was reverted: at
    # current feedback volume it overfit and regressed holdout MRR (0.635 -> 0.563 on
    # the fastapi replay). The file-class/intent prior stays a scoring multiplier
    # (apply_file_class_weight) until there is enough feedback to fit it. See BENCHMARKS.md.
    "was_in_already_have",
    "dwell_seconds",
    "rejected",
)

LAMBDAMART_BOOSTER_NAME = "ranker.lgb.txt"


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


@runtime_checkable
class RankerModel(Protocol):
    def score(self, features: dict[str, float]) -> float: ...

    def to_dict(self) -> dict[str, Any]: ...


class LogisticRanker:
    """Simple logistic model over per-candidate feature vectors."""

    def __init__(self, weights: dict[str, float], bias: float = 0.0) -> None:
        self.weights = weights
        self.bias = bias

    def score(self, features: dict[str, float]) -> float:
        total = self.bias
        for key, weight in self.weights.items():
            total += weight * float(features.get(key, 0.0))
        return total

    def probability(self, features: dict[str, float]) -> float:
        return _sigmoid(self.score(features))

    def to_dict(self) -> dict[str, Any]:
        return {"weights": self.weights, "bias": self.bias, "model": "logistic_v1"}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LogisticRanker:
        return cls(
            weights={str(k): float(v) for k, v in payload.get("weights", {}).items()},
            bias=float(payload.get("bias", 0.0)),
        )


class LambdaMartRanker:
    """LightGBM LambdaMART model (optional ``lightgbm`` extra)."""

    def __init__(self, booster: Any, *, feature_keys: tuple[str, ...] = FEATURE_KEYS) -> None:
        self.booster = booster
        self.feature_keys = feature_keys

    def score(self, features: dict[str, float]) -> float:
        row = [[float(features.get(key, 0.0)) for key in self.feature_keys]]
        return float(self.booster.predict(row)[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": "lambdamart_v1",
            "feature_keys": list(self.feature_keys),
            "booster_file": LAMBDAMART_BOOSTER_NAME,
        }

    @classmethod
    def from_dict(cls, repo_root: Path, payload: dict[str, Any]) -> LambdaMartRanker | None:
        try:
            import lightgbm as lgb
        except ImportError:
            return None
        booster_file = str(payload.get("booster_file", LAMBDAMART_BOOSTER_NAME))
        booster_path = repo_root / DB_DIR / booster_file
        if not booster_path.exists():
            return None
        booster = lgb.Booster(model_file=str(booster_path))
        keys = tuple(str(k) for k in payload.get("feature_keys", FEATURE_KEYS))
        return cls(booster, feature_keys=keys)


def _labelled_events(
    events: list[dict[str, Any]],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    positives: dict[str, set[str]] = {}
    negatives: dict[str, set[str]] = {}
    pools: dict[str, list[dict[str, Any]]] = {}

    for event in events:
        kind = str(event.get("kind", ""))
        request_id = str(event.get("request_id", ""))
        path = str(event.get("path", ""))
        if kind == "context_request":
            pools[request_id] = list(event.get("candidates") or [])
            continue
        if not request_id or not path:
            continue
        if kind in {"cite", "accept", "mark_used"} or (
            kind == "dwell" and float(event.get("dwell_seconds", 0)) >= 30
        ):
            positives.setdefault(request_id, set()).add(path)
        elif kind in {"reject", "view"}:
            negatives.setdefault(request_id, set()).add(path)

    return pools, positives, negatives


def _candidate_features(candidate: dict[str, Any]) -> dict[str, float]:
    features = candidate.get("features") or {}
    if not isinstance(features, dict):
        features = {}
    return {key: float(features.get(key, candidate.get(key, 0.0)) or 0.0) for key in FEATURE_KEYS}


def _training_pairs(events: list[dict[str, Any]]) -> list[tuple[dict[str, float], int]]:
    pools, positives, negatives = _labelled_events(events)
    samples: list[tuple[dict[str, float], int]] = []
    for request_id, candidates in pools.items():
        pos = positives.get(request_id, set())
        neg = negatives.get(request_id, set())
        for candidate in candidates:
            merged = _candidate_features(candidate)
            path = str(candidate.get("path", ""))
            if path in pos:
                samples.append((merged, 1))
            elif path in neg:
                samples.append((merged, 0))
    return samples


def _ltr_matrices(
    events: list[dict[str, Any]],
) -> tuple[list[list[float]], list[int], list[int]]:
    pools, positives, negatives = _labelled_events(events)
    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[int] = []

    for request_id, candidates in pools.items():
        pos = positives.get(request_id, set())
        neg = negatives.get(request_id, set())
        group_count = 0
        for candidate in candidates:
            path = str(candidate.get("path", ""))
            if path in pos:
                label = 1
            elif path in neg:
                label = 0
            else:
                continue
            rows.append([_candidate_features(candidate)[key] for key in FEATURE_KEYS])
            labels.append(label)
            group_count += 1
        if group_count > 0:
            groups.append(group_count)
    return rows, labels, groups


def train_logistic_ranker(
    events: list[dict[str, Any]],
    *,
    epochs: int = 120,
    learning_rate: float = 0.05,
) -> LogisticRanker | None:
    samples = _training_pairs(events)
    if len(samples) < 4:
        return None

    weights = {key: 0.0 for key in FEATURE_KEYS}
    bias = 0.0
    for _ in range(epochs):
        for features, label in samples:
            pred = _sigmoid(bias + sum(weights[k] * features.get(k, 0.0) for k in weights))
            error = float(label) - pred
            bias += learning_rate * error
            for key in weights:
                weights[key] += learning_rate * error * features.get(key, 0.0)
    return LogisticRanker(weights=weights, bias=bias)


def train_lambdamart_ranker(events: list[dict[str, Any]]) -> LambdaMartRanker | None:
    """Train LightGBM LambdaMART when the optional ``lightgbm`` package is installed."""
    try:
        import lightgbm as lgb
    except ImportError:
        return None

    rows, labels, groups = _ltr_matrices(events)
    if len(groups) < 2 or sum(groups) < 6:
        return None

    ranker = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        n_estimators=64,
        learning_rate=0.08,
        num_leaves=12,
        min_child_samples=2,
        verbose=-1,
    )
    ranker.fit(rows, labels, group=groups)
    return LambdaMartRanker(ranker.booster_)


def train_best_ranker(
    events: list[dict[str, Any]],
    *,
    prefer: str = "auto",
    epochs: int = 120,
    learning_rate: float = 0.05,
) -> RankerModel | None:
    """Pick LambdaMART when available, else logistic."""
    mode = prefer.lower()
    if mode in {"auto", "lambdamart"}:
        lambdamart = train_lambdamart_ranker(events)
        if lambdamart is not None:
            return lambdamart
        if mode == "lambdamart":
            return None
    return train_logistic_ranker(events, epochs=epochs, learning_rate=learning_rate)


def learn_file_weights(store_rows: list[tuple[str, int, int]]) -> dict[str, float]:
    weights: dict[str, float] = {}
    for file_path, used_count, total_count in store_rows:
        total = max(1, int(total_count))
        ratio = float(used_count) / total
        ratio = min(0.99, max(0.01, ratio))
        weights[file_path] = math.log(ratio / (1 - ratio))
    return weights


def blend_alpha(event_count: int) -> float:
    if event_count <= 0:
        return 0.0
    return min(0.75, event_count / (event_count + 40.0))


def ranker_path(repo_root: Path) -> Path:
    return repo_root / DB_DIR / "ranker.json"


def lambdamart_booster_path(repo_root: Path) -> Path:
    return repo_root / DB_DIR / LAMBDAMART_BOOSTER_NAME


def load_ranker(repo_root: Path) -> RankerModel | None:
    path = ranker_path(repo_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    model = str(payload.get("model", "logistic_v1"))
    if model == "lambdamart_v1":
        return LambdaMartRanker.from_dict(repo_root, payload)
    return LogisticRanker.from_dict(payload)


def save_ranker(repo_root: Path, ranker: RankerModel) -> Path:
    path = ranker_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ranker.to_dict()
    if isinstance(ranker, LambdaMartRanker):
        ranker.booster.save_model(str(lambdamart_booster_path(repo_root)))
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def apply_ranker_boost(
    prior_score: float,
    features: dict[str, float],
    ranker: RankerModel | None,
    *,
    alpha: float,
) -> float:
    if ranker is None or alpha <= 0:
        return prior_score
    learned = ranker.score(features) * 10.0
    return (alpha * learned) + ((1.0 - alpha) * prior_score)


def lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False
