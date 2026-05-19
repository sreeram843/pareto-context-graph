"""SQLite storage for the co-change graph."""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

DB_DIR = ".code-graph"
DB_NAME = "graph.db"

SCHEMA = """\
CREATE TABLE IF NOT EXISTS files (
    id    INTEGER PRIMARY KEY,
    path  TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS co_changes (
    file_a  INTEGER NOT NULL REFERENCES files(id),
    file_b  INTEGER NOT NULL REFERENCES files(id),
    weight  REAL NOT NULL DEFAULT 1,
    last_seen_ts INTEGER,
    PRIMARY KEY (file_a, file_b)
);

CREATE TABLE IF NOT EXISTS top_neighbours (
    file_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    neighbour_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (file_id, rank)
);

CREATE TABLE IF NOT EXISTS feedback (
    ts INTEGER NOT NULL,
    query TEXT NOT NULL,
    file_path TEXT NOT NULL,
    returned INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_co_a ON co_changes(file_a);
CREATE INDEX IF NOT EXISTS idx_co_b ON co_changes(file_b);
"""

FTS_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(path, content=files, content_rowid=id);
"""

FTS_TRIGGERS = """\
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, path) VALUES (new.id, new.path);
END;
"""


class Store:
    """Thin wrapper around the SQLite graph database."""

    def __init__(self, repo_root: Path) -> None:
        db_path = repo_root / DB_DIR / DB_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.executescript(SCHEMA)
        self._run_migrations()
        # FTS5 for fast file path search (best-effort; old SQLite may lack fts5)
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.conn.executescript(FTS_TRIGGERS)
            self._has_fts = True
        except sqlite3.OperationalError:
            self._has_fts = False

    def _run_migrations(self) -> None:
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(co_changes)").fetchall()
        }
        if "last_seen_ts" not in columns:
            self.conn.execute("ALTER TABLE co_changes ADD COLUMN last_seen_ts INTEGER")
            now_ts = int(time.time())
            self.conn.execute(
                "UPDATE co_changes SET last_seen_ts = ? WHERE last_seen_ts IS NULL",
                (now_ts,),
            )
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def clear(self) -> None:
        """Drop all graph data (files + edges). Used before a full rebuild."""
        self.conn.executescript(
            "DELETE FROM co_changes; DELETE FROM top_neighbours; DELETE FROM files;"
        )
        if self._has_fts:
            self.conn.executescript(
                "DELETE FROM files_fts;"
            )
        self.conn.commit()

    # -- files ----------------------------------------------------------------

    def upsert_file(self, path: str) -> int:
        self.conn.execute(
            "INSERT INTO files(path) VALUES (?) ON CONFLICT(path) DO NOTHING",
            (path,),
        )
        # NOTE: cursor.lastrowid is unreliable with ON CONFLICT DO NOTHING — it
        # returns the connection-level last-insert rowid even when the insert was
        # skipped, causing wrong IDs.  Always resolve via SELECT (indexed lookup).
        row = self.conn.execute(
            "SELECT id FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row[0]

    def file_id(self, path: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM files WHERE path = ?", (path,)
        ).fetchone()
        return row[0] if row else None

    def all_files(self) -> list[str]:
        return [
            r[0] for r in self.conn.execute("SELECT path FROM files").fetchall()
        ]

    # -- co-changes -----------------------------------------------------------

    def record_co_change(
        self,
        path_a: str,
        path_b: str,
        weight: float = 1.0,
        last_seen_ts: int | None = None,
    ) -> None:
        id_a = self.upsert_file(path_a)
        id_b = self.upsert_file(path_b)
        lo, hi = (id_a, id_b) if id_a < id_b else (id_b, id_a)
        seen_ts = int(last_seen_ts or time.time())
        self.conn.execute(
            """INSERT INTO co_changes(file_a, file_b, weight, last_seen_ts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(file_a, file_b)
               DO UPDATE SET
                    weight = co_changes.weight + excluded.weight,
                    last_seen_ts = MAX(COALESCE(co_changes.last_seen_ts, 0), excluded.last_seen_ts)""",
            (lo, hi, weight, seen_ts),
        )

    def neighbours(self, path: str, min_weight: int = 1) -> list[tuple[str, float]]:
        """Return files that co-changed with *path* and their weight."""
        fid = self.file_id(path)
        if fid is None:
            return []
        rows = self.conn.execute(
            """SELECT f.path, c.weight
               FROM co_changes c
               JOIN files f ON f.id = CASE WHEN c.file_a = ? THEN c.file_b ELSE c.file_a END
               WHERE (c.file_a = ? OR c.file_b = ?) AND c.weight >= ?
               ORDER BY c.weight DESC""",
            (fid, fid, fid, min_weight),
        ).fetchall()
        return rows

    def top_neighbours(self, path: str, limit: int = 50) -> list[tuple[str, float]]:
        fid = self.file_id(path)
        if fid is None:
            return []
        rows = self.conn.execute(
            """SELECT f.path, tn.weight
               FROM top_neighbours tn
               JOIN files f ON f.id = tn.neighbour_id
               WHERE tn.file_id = ?
               ORDER BY tn.rank ASC
               LIMIT ?""",
            (fid, limit),
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def rebuild_top_neighbours(self, k: int = 50) -> None:
        self.conn.execute("DELETE FROM top_neighbours")
        ids = [r[0] for r in self.conn.execute("SELECT id FROM files").fetchall()]
        for fid in ids:
            rows = self.conn.execute(
                """SELECT CASE WHEN c.file_a = ? THEN c.file_b ELSE c.file_a END AS neighbour_id,
                         c.weight
                   FROM co_changes c
                   WHERE c.file_a = ? OR c.file_b = ?
                   ORDER BY c.weight DESC
                   LIMIT ?""",
                (fid, fid, fid, k),
            ).fetchall()
            for rank, (neighbour_id, weight) in enumerate(rows, start=1):
                self.conn.execute(
                    "INSERT INTO top_neighbours(file_id, rank, neighbour_id, weight) VALUES (?, ?, ?, ?)",
                    (fid, rank, neighbour_id, float(weight)),
                )
        self.conn.commit()

    def apply_decay(self, half_life_days: float, prune_below: float | None = None) -> int:
        if half_life_days <= 0:
            raise ValueError("half_life_days must be > 0")
        now_ts = int(time.time())
        rows = self.conn.execute(
            "SELECT file_a, file_b, weight, COALESCE(last_seen_ts, ?) FROM co_changes",
            (now_ts,),
        ).fetchall()
        for file_a, file_b, weight, last_seen_ts in rows:
            age_days = max(0.0, (now_ts - int(last_seen_ts)) / 86400.0)
            factor = math.exp(-(age_days / half_life_days))
            new_weight = float(weight) * factor
            self.conn.execute(
                "UPDATE co_changes SET weight = ? WHERE file_a = ? AND file_b = ?",
                (new_weight, file_a, file_b),
            )

        deleted = 0
        if prune_below is not None:
            cur = self.conn.execute("DELETE FROM co_changes WHERE weight < ?", (prune_below,))
            deleted = cur.rowcount if cur.rowcount is not None else 0
        self.conn.commit()
        return deleted

    def commit(self) -> None:
        self.conn.commit()

    # -- meta -----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        self.conn.commit()

    # -- stats ----------------------------------------------------------------

    def file_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()
        return row[0]

    def edge_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM co_changes").fetchone()
        return row[0]

    def node_degrees(self) -> dict[str, int]:
        rows = self.conn.execute(
            """SELECT f.path, COUNT(*) as degree
               FROM files f
               JOIN co_changes c ON c.file_a = f.id OR c.file_b = f.id
               GROUP BY f.id"""
        ).fetchall()
        return {path: int(degree) for path, degree in rows}

    def graph_stats(self) -> dict:
        degrees = self.node_degrees()
        sorted_hubs = sorted(degrees.items(), key=lambda item: item[1], reverse=True)
        p95 = 0
        if degrees:
            values = sorted(degrees.values())
            idx = int(0.95 * (len(values) - 1))
            p95 = values[idx]
        return {
            "files": self.file_count(),
            "edges": self.edge_count(),
            "p95_degree": p95,
            "top_hubs": [{"path": p, "degree": d} for p, d in sorted_hubs[:10]],
        }

    def log_feedback(self, query: str, file_path: str, returned: bool = True, used: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO feedback(ts, query, file_path, returned, used) VALUES (?, ?, ?, ?, ?)",
            (int(time.time()), query, file_path, int(returned), int(used)),
        )
        self.conn.commit()

    def mark_feedback_used(self, paths: list[str]) -> int:
        if not paths:
            return 0
        placeholders = ",".join("?" for _ in paths)
        cur = self.conn.execute(
            f"UPDATE feedback SET used = 1 WHERE file_path IN ({placeholders})",
            tuple(paths),
        )
        self.conn.commit()
        return cur.rowcount if cur.rowcount is not None else 0

    # -- hotspots (hub/bridge detection) --------------------------------------

    def get_hotspots(self, top_n: int = 10) -> list[dict]:
        """Find files with highest total coupling (degree centrality).

        These are architectural hubs — files that co-change with many others.
        High-degree nodes are coupling risks or key integration points.
        """
        rows = self.conn.execute(
            """SELECT f.path,
                      COUNT(*) as degree,
                      SUM(c.weight) as total_weight
               FROM files f
               JOIN co_changes c ON c.file_a = f.id OR c.file_b = f.id
               GROUP BY f.id
               ORDER BY total_weight DESC
               LIMIT ?""",
            (top_n,),
        ).fetchall()
        return [
            {"path": path, "degree": degree, "total_weight": total_weight}
            for path, degree, total_weight in rows
        ]

    # -- search ---------------------------------------------------------------

    def search_files(self, pattern: str, limit: int = 20) -> list[str]:
        """Search file paths by FTS5 (fast) or LIKE fallback."""
        if self._has_fts:
            # FTS5 tokenizes on / so "server" matches paths containing "server"
            query = " OR ".join(pattern.split())
            rows = self.conn.execute(
                "SELECT path FROM files_fts WHERE files_fts MATCH ? LIMIT ?",
                (query, limit),
            ).fetchall()
            if rows:
                return [r[0] for r in rows]
        # Fallback to LIKE
        rows = self.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? LIMIT ?",
            (f"%{pattern}%", limit),
        ).fetchall()
        return [r[0] for r in rows]

    # -- community detection --------------------------------------------------

    def get_communities(self, min_weight: int = 3, max_community_size: int = 50) -> list[list[str]]:
        """Find implicit modules via connected components on strong edges.

        Files connected by edges >= min_weight form a community (cluster).
        This reveals implicit modules that aren't reflected in the directory structure.
        """
        total_files = self.file_count()
        threshold = min_weight

        while True:
            rows = self.conn.execute(
                """SELECT f1.path, f2.path
                   FROM co_changes c
                   JOIN files f1 ON f1.id = c.file_a
                   JOIN files f2 ON f2.id = c.file_b
                   WHERE c.weight >= ?""",
                (threshold,),
            ).fetchall()

            adj: dict[str, set[str]] = {}
            for a, b in rows:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)

            visited: set[str] = set()
            communities: list[list[str]] = []
            for node in adj:
                if node in visited:
                    continue
                component: list[str] = []
                queue = [node]
                while queue:
                    current = queue.pop()
                    if current in visited:
                        continue
                    visited.add(current)
                    component.append(current)
                    if len(component) >= max_community_size:
                        break
                    for neighbour in adj.get(current, set()):
                        if neighbour not in visited:
                            queue.append(neighbour)
                if len(component) >= 2:
                    communities.append(sorted(component))

            largest = max((len(c) for c in communities), default=0)
            if total_files == 0 or largest <= max(total_files * 0.3, max_community_size) or threshold >= 10:
                communities.sort(key=len, reverse=True)
                return communities
            threshold += 1
