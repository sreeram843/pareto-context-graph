"""Append-only feedback events and batched fold into SQLite."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .repo_caches import invalidate_caches
from .store import DB_DIR, Store

POSITIVE_KINDS = frozenset({"cite", "accept", "mark_used"})
NEGATIVE_KINDS = frozenset({"reject"})

LEARNING_ARTIFACTS = (
    "events.jsonl",
    "weights.json",
    "prune_weights.json",
    "ranker.json",
    "ranker.lgb.txt",
)


def _events_path(repo_root: Path) -> Path:
    return repo_root / DB_DIR / "events.jsonl"


def event_key(request_id: str, kind: str, path: str) -> str:
    return f"{request_id}:{kind}:{path}"


class FeedbackEventLog:
    """Thread-safe append-only event log with idempotent writes."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.path = _events_path(repo_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: dict[str, Any], *, dedupe: bool = True) -> bool:
        """Append an event. Returns False when deduped as duplicate."""
        payload = dict(event)
        payload.setdefault("ts", int(time.time()))
        kind = str(payload.get("kind", ""))
        request_id = str(payload.get("request_id", ""))
        path = str(payload.get("path", ""))
        if dedupe and request_id and path and kind:
            store = Store(self.repo_root)
            try:
                if store.has_feedback_dedup(event_key(request_id, kind, path)):
                    return False
                store.add_feedback_dedup(event_key(request_id, kind, path))
                store.commit()
            finally:
                store.close()

        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return True

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


def fold_events_to_sqlite(repo_root: Path) -> dict[str, int]:
    """Fold append-only events into the feedback SQLite table."""
    logger = FeedbackEventLog(repo_root)
    events = logger.read_all()
    if not events:
        return {"processed": 0, "positive": 0, "negative": 0}

    store = Store(repo_root)
    stats = {"processed": 0, "positive": 0, "negative": 0}
    try:
        for event in events:
            kind = str(event.get("kind", ""))
            if kind == "context_request":
                continue
            query = str(event.get("query", ""))
            paths = list(event.get("paths") or [])
            if event.get("path"):
                paths.append(str(event["path"]))
            if kind == "dwell" and float(event.get("dwell_seconds", 0)) < 30:
                continue
            positive = (
                kind in POSITIVE_KINDS
                or kind == "cite"
                or (kind == "dwell" and float(event.get("dwell_seconds", 0)) >= 30)
            )
            for path in paths:
                if not path:
                    continue
                if positive:
                    store.log_feedback(
                        query=query or kind, file_path=path, returned=True, used=True
                    )
                    stats["positive"] += 1
                elif kind in NEGATIVE_KINDS:
                    store.log_feedback(
                        query=query or kind, file_path=path, returned=True, used=False
                    )
                    stats["negative"] += 1
                elif kind == "view":
                    store.log_feedback(
                        query=query or kind, file_path=path, returned=True, used=False
                    )
                stats["processed"] += 1
        store.commit()
    finally:
        store.close()
    return stats


def feedback_path_signals(repo_root: Path) -> dict[str, dict[str, float]]:
    """Per-path feedback aggregates for ranker features (Phase 11.5)."""
    signals: dict[str, dict[str, float]] = {}
    for event in FeedbackEventLog(repo_root).read_all():
        path = str(event.get("path", ""))
        if not path:
            continue
        row = signals.setdefault(path, {"dwell_seconds": 0.0, "rejected": 0.0})
        kind = str(event.get("kind", ""))
        if kind == "dwell":
            row["dwell_seconds"] = max(row["dwell_seconds"], float(event.get("dwell_seconds", 0.0)))
        elif kind in NEGATIVE_KINDS:
            row["rejected"] = 1.0
    return signals


class FeedbackFlusher:
    """Background worker that folds events.jsonl into SQLite periodically."""

    def __init__(self, repo_root: Path, interval: int = 30) -> None:
        self.repo_root = repo_root
        self.interval = max(1, int(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                fold_events_to_sqlite(self.repo_root)
            except Exception:
                pass
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            fold_events_to_sqlite(self.repo_root)
        except Exception:
            pass


def feedback_log_enabled(arguments: dict) -> bool:
    """When False, skip counterfactual event logging (eval tier-3 probes)."""
    if "feedback_log" in arguments:
        return bool(arguments["feedback_log"])
    return True


def log_context_request(
    repo_root: Path,
    *,
    request_id: str,
    query: str,
    seed_files: list[str],
    candidates: list[dict[str, Any]],
    returned_paths: list[str],
) -> None:
    """Counterfactual log of ranked pool + returned subset."""
    logger = FeedbackEventLog(repo_root)
    logger.append(
        {
            "kind": "context_request",
            "request_id": request_id,
            "query": query,
            "seed_files": seed_files,
            "candidates": candidates,
            "returned_paths": returned_paths,
        },
        dedupe=False,
    )


def record_feedback(
    repo_root: Path,
    *,
    kind: str,
    request_id: str,
    paths: list[str],
    query: str = "",
    dwell_seconds: float | None = None,
) -> dict[str, int]:
    """Record client feedback events (idempotent per request_id/path/kind)."""
    logger = FeedbackEventLog(repo_root)
    written = 0
    deduped = 0
    for path in paths:
        event: dict[str, Any] = {
            "kind": kind,
            "request_id": request_id,
            "path": path,
            "query": query,
        }
        if dwell_seconds is not None:
            event["dwell_seconds"] = dwell_seconds
        if logger.append(event, dedupe=True):
            written += 1
        else:
            deduped += 1
    return {"written": written, "deduped": deduped}


def clear_learning_state(repo_root: Path) -> None:
    """Remove learned artifacts and feedback tables for reproducible eval."""
    from .session import clear_session

    db_dir = repo_root / DB_DIR
    for name in LEARNING_ARTIFACTS:
        path = db_dir / name
        if path.exists():
            path.unlink()
    clear_session(repo_root)
    store = Store(repo_root)
    try:
        store.conn.execute("DELETE FROM feedback")
        store.conn.execute("DELETE FROM feedback_dedup")
        store.commit()
    finally:
        store.close()
    invalidate_caches()
