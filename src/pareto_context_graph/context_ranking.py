"""Context pipeline ranking, semantic search, and packing helpers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import cast

from .blast import blast_radius, extract_imports, get_file_summary
from .chunks import KeywordIndex, get_relevant_chunks, get_signatures
from .deadlines import deadline_tick
from .repo_caches import (
    degree_cache_slot,
    get_repo_cached,
    keyword_cache_slot,
    learned_weights_cache_slot,
)
from .store import Store
from .taxonomy import file_class, is_test_file
from .tokens import estimate_file_tokens
from .walk import random_walk_with_restart


def mirror_key(path: str) -> tuple[str, str]:
    """Normalize impl/spec variants into the same ranking bucket."""
    pure = Path(path)
    stem = pure.stem.lower()
    for suffix in ("_spec", "_test", ".spec", ".test"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem.startswith("test_"):
        stem = stem[5:]
    # Family id is the normalized stem so impl/test paths in different dirs still pair.
    return "", stem


def build_mirror_groups(paths: list[str]) -> dict[str, dict[str, bool]]:
    """Track whether a normalized file family has test and/or non-test members."""
    groups: dict[str, dict[str, bool]] = {}
    for path in paths:
        key = "::".join(mirror_key(path))
        entry = groups.setdefault(key, {"has_test": False, "has_non_test": False})
        if is_test_file(path):
            entry["has_test"] = True
        else:
            entry["has_non_test"] = True
    return groups


def apply_file_class_weight(base_score: float, path: str, query_intent: str) -> float:
    """Apply lightweight path-class weighting for the current query intent."""
    kind = file_class(path)
    path_lower = path.lower()

    if kind == "generated":
        return base_score * 0.01
    if kind == "fixture":
        return base_score * 0.1

    if query_intent == "endpoint":
        if kind == "route":
            return base_score * 2.5
        if kind == "source" and any(
            token in path_lower
            for token in ("controller", "model", "service", "serializer", "patient")
        ):
            return base_score * 1.4
        if kind == "doc":
            return base_score * 0.2
    elif query_intent == "schema":
        if kind == "migration":
            return base_score * 2.0
        if kind == "route":
            return base_score * 0.5
    elif query_intent == "docs":
        if kind == "doc":
            return base_score * 2.0
    elif query_intent == "openapi":
        if "/openapi/" in path_lower:
            multiplier = 3.5
            if path_lower.endswith("openapi/models.py"):
                multiplier = 4.5
            return base_score * multiplier
        pure = PurePosixPath(path_lower)
        if pure.parts and pure.parts[0] == "fastapi" and "/openapi/" not in path_lower:
            if len(pure.parts) == 2 or (len(pure.parts) == 3 and pure.parts[1] == "_compat"):
                return base_score * 0.35
        if path_lower.endswith("routing.py") or kind == "route":
            return base_score * 0.5
    elif query_intent == "test":
        if kind == "test":
            return base_score * 2.0

    if kind == "doc":
        return base_score * 0.5
    return base_score


def all_repo_files(repo_root: Path) -> set[str]:
    """List repository files for cold-start fallback when the graph is sparse."""
    paths: set[str] = set()
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if (
            rel.startswith(".git/")
            or rel.startswith(".pareto-context-graph/")
            or rel.startswith(".venv/")
        ):
            continue
        paths.add(rel)
    return paths


def shared_path_depth(candidate: str, seed_files: list[str]) -> int:
    """Return max number of shared directory components between candidate and any seed."""
    cand_parts = Path(candidate).parent.parts
    best = 0
    for seed in seed_files:
        seed_parts = Path(seed).parent.parts
        shared = sum(1 for _ in zip(cand_parts, seed_parts) if _ == (_[0], _[0]))
        # zip gives (a,b) pairs; we need to count leading matches
        shared = 0
        for a, b in zip(cand_parts, seed_parts):
            if a == b:
                shared += 1
            else:
                break
        best = max(best, shared)
    return best


def locality_multiplier(candidate: str, seed_files: list[str]) -> float:
    """Score multiplier based on directory proximity to the nearest seed file.

    Same directory      → 3.0×
    One level up        → 1.8×
    Two levels up       → 1.3×
    Deeper mismatch     → 1.0× (no change)
    """
    if not seed_files:
        return 1.0
    depth = shared_path_depth(candidate, seed_files)
    if depth == 0:
        return 1.0
    seed_depths = [len(Path(s).parent.parts) for s in seed_files]
    max_seed_depth = max(seed_depths) if seed_depths else 1
    gap = max(0, max_seed_depth - depth)
    if gap == 0:
        return 3.0
    if gap == 1:
        return 1.8
    if gap == 2:
        return 1.3
    return 1.0


def build_keyword_index(repo_root: Path, store: Store) -> KeywordIndex:
    """Build or return cached keyword index for semantic matching."""

    def _factory() -> KeywordIndex:
        idx = KeywordIndex()
        for file_path in store.all_files():
            fp = repo_root / file_path
            if fp.is_file() and fp.stat().st_size < 100000:
                try:
                    content = fp.read_text(errors="ignore")[:20000]
                    idx.index_file(file_path, content)
                except OSError:
                    pass
        return idx

    return cast(KeywordIndex, get_repo_cached(keyword_cache_slot(), repo_root, _factory))


def semantic_search_capped_tfidf(
    repo_root: Path,
    store: Store,
    query: str,
    *,
    top_n: int,
) -> list[tuple[str, float]]:
    """TF-IDF over path/symbol candidates only (large-repo fallback without full index scan)."""
    candidates: list[str] = []
    seen: set[str] = set()
    for path in store.search_files(query, limit=top_n * 3):
        if path not in seen:
            candidates.append(path)
            seen.add(path)
    for path, _score, _symbol, _line in store.search_symbols(query, limit=top_n * 2):
        if path not in seen:
            candidates.append(path)
            seen.add(path)
    if not candidates:
        return []

    idx = KeywordIndex()
    for file_path in candidates[: top_n * 4]:
        fp = repo_root / file_path
        if not fp.is_file() or fp.stat().st_size >= 100_000:
            continue
        try:
            idx.index_file(file_path, fp.read_text(errors="ignore")[:20_000])
        except OSError:
            continue
    return idx.query(query, top_n=top_n)


def semantic_search(
    repo_root: Path,
    store: Store,
    query: str,
    top_n: int = 15,
    *,
    prefer_bm25: bool = False,
    large_graph: bool = False,
) -> tuple[list[tuple[str, float]], dict[str, object]]:
    """BM25 when preferred and indexed; otherwise in-memory TF-IDF fallback."""
    meta: dict[str, object] = {
        "backend": "tfidf_full",
        "prefer_bm25": prefer_bm25,
        "index_present": store.has_search_index(),
        "bm25_empty_fallback": False,
    }
    if prefer_bm25 and store.has_search_index():
        results = store.search_content_bm25(query, limit=top_n)
        if results:
            meta["backend"] = "bm25"
            return results, meta
        meta["bm25_empty_fallback"] = True
    if large_graph:
        meta["backend"] = "tfidf_capped"
        return semantic_search_capped_tfidf(repo_root, store, query, top_n=top_n), meta
    kw_index = build_keyword_index(repo_root, store)
    return kw_index.query(query, top_n=top_n), meta


def node_degrees(repo_root: Path, store: Store) -> dict[str, int]:
    return get_repo_cached(degree_cache_slot(), repo_root, lambda: store.node_degrees())


def learned_weights(repo_root: Path) -> dict[str, float]:
    weights_path = repo_root / ".pareto-context-graph" / "weights.json"
    if weights_path.is_file():
        st = weights_path.stat()
        weights_ver = f":w={st.st_mtime_ns}:{st.st_size}"
    else:
        weights_ver = ":w=missing"

    def _factory() -> dict[str, float]:
        if not weights_path.exists():
            return {}
        try:
            payload = json.loads(weights_path.read_text())
        except Exception:
            return {}
        return {str(k): float(v) for k, v in payload.items()}

    return get_repo_cached(
        learned_weights_cache_slot(),
        repo_root,
        _factory,
        extra_version=weights_ver,
    )


def stage1_candidates(
    store: Store,
    seed_files: list[str],
    *,
    min_weight: int,
    max_depth: int,
    cap: int,
    expansion: str,
    expired_check: Callable[[], bool] | None = None,
) -> list[dict]:
    if expansion == "rwr":
        walk_scores = random_walk_with_restart(
            store,
            seed_files,
            walks=200,
            length=max_depth + 4,
            restart=0.15,
            expired_check=expired_check,
        )
        ranked = sorted(walk_scores.items(), key=lambda item: item[1], reverse=True)
        out: list[dict] = []
        for idx, (path, score) in enumerate(ranked):
            if expired_check and deadline_tick(idx) and expired_check():
                break
            if path in seed_files:
                continue
            out.append(
                {"path": path, "depth": 1, "weight": max(1, int(score * 1000)), "signal": "rwr"}
            )
            if len(out) >= cap:
                break
        return out

    results = blast_radius(
        store,
        seed_files,
        min_weight=min_weight,
        max_depth=max_depth,
        max_results=cap,
        use_cache=True,
        expired_check=expired_check,
    )
    filtered = [r for r in results if r.get("path") not in set(seed_files)]
    return filtered[:cap]


def dir_tokens(path: str) -> set[str]:
    return {part for part in Path(path).parts if part}


def similarity_for_mmr(
    path_a: str, symbols_a: list[str], path_b: str, symbols_b: list[str]
) -> float:
    a_tokens = dir_tokens(path_a)
    b_tokens = dir_tokens(path_b)
    union = len(a_tokens | b_tokens) or 1
    path_jaccard = len(a_tokens & b_tokens) / union
    sym_a = set(symbols_a)
    sym_b = set(symbols_b)
    sym_union = len(sym_a | sym_b) or 1
    sym_jaccard = len(sym_a & sym_b) / sym_union
    return 0.7 * path_jaccard + 0.3 * sym_jaccard


def mmr_select(
    candidates: list[dict],
    limit: int,
    mmr_lambda: float,
    *,
    expired_check: Callable[[], bool] | None = None,
) -> list[dict]:
    if not candidates:
        return []
    remaining = candidates[:]
    selected: list[dict] = []
    iteration = 0

    while remaining and len(selected) < limit:
        iteration += 1
        if expired_check and deadline_tick(iteration) and expired_check():
            break
        best_idx = 0
        best_score = float("-inf")
        for idx, cand in enumerate(remaining):
            relevance = float(cand.get("_relevance", cand.get("weight", 0)))
            if not selected:
                mmr = relevance
            else:
                max_sim = 0.0
                for chosen in selected:
                    sim = similarity_for_mmr(
                        cand["path"],
                        cand.get("_symbols", []),
                        chosen["path"],
                        chosen.get("_symbols", []),
                    )
                    max_sim = max(max_sim, sim)
                mmr = (mmr_lambda * relevance) - ((1.0 - mmr_lambda) * max_sim)
            if mmr > best_score:
                best_score = mmr
                best_idx = idx
        selected.append(remaining.pop(best_idx))

    return selected


PRIVATE_SIGNATURE = re.compile(
    r"\bdef _\w|\bclass _\w|\bprivate\b|\bprotected\b",
    re.IGNORECASE,
)


def filter_signatures(signatures: list[str], compression: str) -> list[str]:
    capped = signatures[:20]
    if compression != "lossy":
        return capped
    public = [sig for sig in capped if not PRIVATE_SIGNATURE.search(sig)]
    return public if public else capped[:10]


def redact_entry_fields(entry: dict) -> None:
    from .hooks import redact_text

    content = entry.get("content")
    if isinstance(content, str):
        entry["content"] = redact_text(content)
    for chunk in entry.get("chunks", []):
        body = chunk.get("body")
        if isinstance(body, str):
            chunk["body"] = redact_text(body)


def build_context_entry(
    r: dict,
    repo_root: Path,
    tier: int,
    query: str,
    files: list[str],
    compression: str,
) -> dict:
    fp = repo_root / r["path"]
    entry: dict = {"path": r["path"]}
    if r.get("signal"):
        entry["signal"] = r["signal"]
    file_tokens = r.get("tokens", estimate_file_tokens(fp))

    if tier == 1:
        entry["summary"] = get_file_summary(fp)
        entry["tokens"] = file_tokens
    elif tier == 2:
        entry["signatures"] = filter_signatures(get_signatures(fp), compression)
        entry["tokens"] = file_tokens
    else:
        seed_imports: list[str] = []
        for seed in files:
            seed_path = repo_root / seed
            if seed_path.is_file():
                seed_imports.extend(extract_imports(seed_path))
        chunks = get_relevant_chunks(fp, query=query, seed_imports=seed_imports)
        if chunks:
            entry["chunks"] = [
                {
                    "name": c["name"],
                    "lines": f"{c['start_line']}-{c['end_line']}",
                    "body": c["body"],
                }
                for c in chunks
            ]
        else:
            try:
                content = fp.read_text(errors="ignore")
                if len(content) > 10000:
                    content = content[:10000] + "\n# ... truncated (use tier=2 for overview)"
                entry["content"] = content
            except OSError:
                entry["content"] = ""
        entry["tokens"] = file_tokens
    return entry


def entry_diagnostics(
    r: dict,
    *,
    files: list[str],
    node_degrees: dict[str, int],
    learned: dict[str, float],
    embed_scores: dict[str, float],
    hub_penalty_strength: float,
) -> dict:
    path = r["path"]
    degree = node_degrees.get(path, 0)
    hub_penalty = math.log2(2 + degree)
    diag: dict = {
        "co_change": r.get("weight"),
        "embed": round(0.15 * embed_scores.get(path, 0.0), 4),
        "locality": round(locality_multiplier(path, files), 4),
        "hub_penalty": round(hub_penalty, 4),
        "learned_boost": round(learned.get(path, 0.0), 4),
        "rank_score": round(float(r.get("_relevance", r.get("weight", 0))), 4),
    }
    features = r.get("_features")
    if isinstance(features, dict):
        for key in ("path", "symbol", "bm25", "embed", "co_change"):
            if key in features:
                diag[key] = features[key]
    if r.get("_orchestrator_score") is not None:
        diag["orchestrator_score"] = r["_orchestrator_score"]
    if r.get("signal"):
        diag["signal"] = r["signal"]
    if r.get("_community_boost"):
        diag["community_boost"] = r["_community_boost"]
    diag["hub_penalty_strength"] = hub_penalty_strength
    return diag


def compress_pick_reason(
    r: dict,
    *,
    files: list[str],
    node_degrees: dict[str, int],
    learned: dict[str, float],
    embed_scores: dict[str, float],
    hub_penalty_strength: float,
) -> str:
    """One-line human-readable reason a file was ranked (tier-1 default)."""
    parts: list[str] = []
    signal = r.get("signal")
    if signal:
        parts.append(str(signal))
    weight = r.get("weight")
    if weight and int(weight) > 1:
        parts.append(f"co-change w={int(weight)}")
    diag = entry_diagnostics(
        r,
        files=files,
        node_degrees=node_degrees,
        learned=learned,
        embed_scores=embed_scores,
        hub_penalty_strength=hub_penalty_strength,
    )
    for key, label in (("bm25", "bm25"), ("symbol", "symbol"), ("embed", "embed")):
        val = diag.get(key)
        if val is not None and float(val) > 0:
            parts.append(f"{label}={val}")
    orch = diag.get("orchestrator_score")
    if orch is not None:
        parts.append(f"orc={orch}")
    if not parts:
        rank = diag.get("rank_score")
        if rank is not None:
            parts.append(f"score={rank}")
        else:
            parts.append("ranked")
    return " + ".join(parts)


def candidate_features(
    r: dict,
    *,
    files: list[str],
    node_degrees: dict[str, int],
    learned: dict[str, float],
    embed_scores: dict[str, float],
    hub_penalty_strength: float,
    already_have: set[str] | None = None,
    feedback_signals: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    diag = entry_diagnostics(
        r,
        files=files,
        node_degrees=node_degrees,
        learned=learned,
        embed_scores=embed_scores,
        hub_penalty_strength=hub_penalty_strength,
    )
    path = str(r.get("path", ""))
    feedback = (feedback_signals or {}).get(path, {})
    features: dict[str, float] = {
        "co_change": float(diag.get("co_change") or 0.0),
        "bm25": float(diag.get("bm25") or 0.0),
        "symbol": float(diag.get("symbol") or 0.0),
        "embed": float(diag.get("embed") or 0.0),
        "locality": float(diag.get("locality") or 0.0),
        "hub_penalty": float(diag.get("hub_penalty") or 0.0),
        "learned_boost": float(diag.get("learned_boost") or 0.0),
        "rank_score": float(diag.get("rank_score") or 0.0),
        "was_in_already_have": 1.0 if path in (already_have or set()) else 0.0,
        "dwell_seconds": float(feedback.get("dwell_seconds", 0.0)),
        "rejected": float(feedback.get("rejected", 0.0)),
    }
    return features
