"""SQLite storage for the co-change graph."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

from .neighbour_cache import compute_top_neighbours_from_edges

DB_DIR = ".pareto-context-graph"
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

CREATE TABLE IF NOT EXISTS feedback_dedup (
    event_key TEXT PRIMARY KEY,
    ts        INTEGER NOT NULL
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

SEARCH_INDEX_SCHEMA = """\
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'symbol',
    line INTEGER NOT NULL DEFAULT 1,
    container TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(symbol);

CREATE TABLE IF NOT EXISTS structural_edges (
    src_path TEXT NOT NULL,
    dst_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'INFERRED',
    PRIMARY KEY (src_path, dst_path, kind)
);
CREATE INDEX IF NOT EXISTS idx_structural_src ON structural_edges(src_path);
CREATE INDEX IF NOT EXISTS idx_structural_dst ON structural_edges(dst_path);

CREATE TABLE IF NOT EXISTS index_state (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS spec_index_state (
    path TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL
);
"""


def _fts_escape_term(term: str) -> str:
    cleaned = re.sub(r'["*]', "", term)
    return cleaned or term


def _fts_query(text: str) -> str:
    """Build a safe FTS5 MATCH expression from user text."""
    terms = re.findall(r"[a-zA-Z]\w{2,}", text)
    if not terms:
        return '""'
    return " OR ".join(f'"{_fts_escape_term(term)}"' for term in terms[:16])


class Store:
    """Thin wrapper around the SQLite graph database."""

    def __init__(self, repo_root: Path, *, readonly: bool = False) -> None:
        self.repo_root = Path(repo_root)
        self.readonly = readonly
        db_path = self.repo_root / DB_DIR / DB_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if readonly and db_path.exists():
            self.conn = sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        self._owns_connection = True
        self._cold_bulk_load = False
        if readonly:
            self._init_reader_flags()
        else:
            self.conn.executescript(SCHEMA)
            self._run_migrations()
            self._init_writer_indexes()

    def _table_exists(self, name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (name,),
        ).fetchone()
        return row is not None

    def _init_reader_flags(self) -> None:
        self._has_fts = self._table_exists("files_fts")
        self._has_symbols_fts = self._table_exists("symbols_fts")
        self._has_content_fts = self._table_exists("content_fts")
        self._has_search_paths_fts = self._table_exists("search_paths_fts")
        self._has_specs_fts = self._table_exists("specs_fts")

    def _init_writer_indexes(self) -> None:
        # FTS5 for fast file path search (best-effort; old SQLite may lack fts5)
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.conn.executescript(FTS_TRIGGERS)
            self._has_fts = True
        except sqlite3.OperationalError:
            self._has_fts = False
        self._has_symbols_fts = False
        self._has_content_fts = False
        self._has_search_paths_fts = False
        self._has_specs_fts = False
        self.conn.executescript(SEARCH_INDEX_SCHEMA)
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5("
                "symbol, kind, path UNINDEXED, line UNINDEXED, tokenize='unicode61')"
            )
            self._has_symbols_fts = True
        except sqlite3.OperationalError:
            self._has_symbols_fts = False
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5("
                "path UNINDEXED, body, tokenize='porter unicode61')"
            )
            self._has_content_fts = True
        except sqlite3.OperationalError:
            self._has_content_fts = False
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_paths_fts USING fts5("
                "path, tokenize='unicode61')"
            )
            self._has_search_paths_fts = True
        except sqlite3.OperationalError:
            self._has_search_paths_fts = False
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS specs_fts USING fts5("
                "path UNINDEXED, kind UNINDEXED, title, body, tokenize='porter unicode61')"
            )
            self._has_specs_fts = True
        except sqlite3.OperationalError:
            self._has_specs_fts = False

    def _run_migrations(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(co_changes)").fetchall()}
        if "last_seen_ts" not in columns:
            self.conn.execute("ALTER TABLE co_changes ADD COLUMN last_seen_ts INTEGER")
            now_ts = int(time.time())
            self.conn.execute(
                "UPDATE co_changes SET last_seen_ts = ? WHERE last_seen_ts IS NULL",
                (now_ts,),
            )
            self.conn.commit()

    def close(self) -> None:
        if getattr(self, "_owns_connection", True):
            self.conn.close()

    def _write_guard(self) -> None:
        if self.readonly:
            raise sqlite3.OperationalError("attempt to write on read-only store connection")

    def clear(self) -> None:
        """Drop all graph data (files + edges). Used before a full rebuild."""
        self.conn.executescript(
            "DELETE FROM co_changes; DELETE FROM top_neighbours; DELETE FROM files;"
        )
        if self._has_fts:
            self.conn.executescript("DELETE FROM files_fts;")
        self.clear_search_indexes()
        if self._table_exists("index_state"):
            self.conn.execute("DELETE FROM index_state")
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
        row = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        return row[0]

    def file_id(self, path: str) -> int | None:
        row = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    def all_files(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT path FROM files").fetchall()]

    # -- co-changes -----------------------------------------------------------

    def record_co_change(
        self,
        path_a: str,
        path_b: str,
        weight: float = 1.0,
        last_seen_ts: int | None = None,
    ) -> None:
        self.record_co_changes_bulk([(path_a, path_b, weight, int(last_seen_ts or time.time()))])

    def record_co_changes_bulk(
        self,
        edges: list[tuple[str, str, float, int]],
    ) -> None:
        """Insert or accumulate many co-change edges in one transaction."""
        if not edges:
            return
        paths: set[str] = set()
        for path_a, path_b, _, _ in edges:
            paths.add(path_a)
            paths.add(path_b)
        self.conn.executemany(
            "INSERT INTO files(path) VALUES (?) ON CONFLICT(path) DO NOTHING",
            [(path,) for path in sorted(paths)],
        )
        placeholders = ",".join("?" * len(paths))
        path_to_id = {
            row[1]: row[0]
            for row in self.conn.execute(
                f"SELECT id, path FROM files WHERE path IN ({placeholders})",
                list(paths),
            ).fetchall()
        }
        rows: list[tuple[int, int, float, int]] = []
        for path_a, path_b, weight, last_seen_ts in edges:
            id_a = path_to_id[path_a]
            id_b = path_to_id[path_b]
            lo, hi = (id_a, id_b) if id_a < id_b else (id_b, id_a)
            rows.append((lo, hi, weight, last_seen_ts))
        self.conn.executemany(
            """INSERT INTO co_changes(file_a, file_b, weight, last_seen_ts)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(file_a, file_b)
               DO UPDATE SET
                    weight = co_changes.weight + excluded.weight,
                    last_seen_ts = MAX(COALESCE(co_changes.last_seen_ts, 0), excluded.last_seen_ts)""",
            rows,
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

    def file_degree(self, path: str) -> int:
        """Co-change degree for a single file (cheaper than full node_degrees)."""
        fid = self.file_id(path)
        if fid is None:
            return 0
        row = self.conn.execute(
            "SELECT COUNT(*) FROM co_changes WHERE file_a = ? OR file_b = ?",
            (fid, fid),
        ).fetchone()
        return int(row[0]) if row else 0

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

    def enter_cold_bulk_load(self) -> None:
        """Speed up cold builds: defer index maintenance until bulk insert completes."""
        if self.readonly or self._cold_bulk_load:
            return
        self._cold_bulk_load = True
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=0")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-200000")
        self.conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        self.conn.execute("DROP INDEX IF EXISTS idx_co_a")
        self.conn.execute("DROP INDEX IF EXISTS idx_co_b")

    def exit_cold_bulk_load(self) -> None:
        """Restore WAL mode and co-change indexes after a cold bulk load."""
        if self.readonly or not self._cold_bulk_load:
            return
        self.conn.commit()
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_co_a ON co_changes(file_a)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_co_b ON co_changes(file_b)")
        self.conn.commit()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA locking_mode=NORMAL")
        self._cold_bulk_load = False
        self.conn.commit()

    @staticmethod
    def cold_bulk_load_enabled() -> bool:
        return os.environ.get("PCG_COLD_BUILD_FAST", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def write_top_neighbours(self, ranked: dict[str, list[tuple[str, float]]]) -> None:
        """Persist pre-computed top-neighbour rows keyed by repo-relative paths."""
        self.conn.execute("DELETE FROM top_neighbours")
        if not ranked:
            return
        paths: set[str] = set(ranked)
        for neighbours in ranked.values():
            for neighbour, _weight in neighbours:
                paths.add(neighbour)
        placeholders = ",".join("?" * len(paths))
        path_to_id = {
            row[1]: row[0]
            for row in self.conn.execute(
                f"SELECT id, path FROM files WHERE path IN ({placeholders})",
                sorted(paths),
            ).fetchall()
        }
        rows: list[tuple[int, int, int, float]] = []
        for path, neighbours in ranked.items():
            file_id = path_to_id.get(path)
            if file_id is None:
                continue
            for rank, (neighbour, weight) in enumerate(neighbours, start=1):
                neighbour_id = path_to_id.get(neighbour)
                if neighbour_id is None:
                    continue
                rows.append((file_id, rank, neighbour_id, weight))
        if rows:
            self.conn.executemany(
                "INSERT INTO top_neighbours (file_id, rank, neighbour_id, weight) VALUES (?, ?, ?, ?)",
                rows,
            )

    def rebuild_top_neighbours(self, k: int = 50) -> None:
        """Rebuild top-neighbour cache in Python (linear scan, no SQL window sort)."""
        edge_rows = self.conn.execute(
            """
            SELECT fa.path, fb.path, cc.weight
            FROM co_changes cc
            JOIN files fa ON fa.id = cc.file_a
            JOIN files fb ON fb.id = cc.file_b
            """
        ).fetchall()
        ranked = compute_top_neighbours_from_edges(
            [(str(a), str(b), float(w)) for a, b, w in edge_rows],
            k=k,
        )
        self.write_top_neighbours(ranked)
        self.conn.commit()

    def apply_decay(self, half_life_days: float, prune_below: float | None = None) -> int:
        if half_life_days <= 0:
            raise ValueError("half_life_days must be > 0")
        now_ts = int(time.time())
        self.conn.execute(
            """
            UPDATE co_changes
            SET weight = weight * exp(
                -(
                    MAX(0.0, (? - COALESCE(last_seen_ts, ?)) / 86400.0)
                    / ?
                )
            )
            """,
            (now_ts, now_ts, half_life_days),
        )

        deleted = 0
        if prune_below is not None:
            cur = self.conn.execute("DELETE FROM co_changes WHERE weight < ?", (prune_below,))
            deleted = cur.rowcount if cur.rowcount is not None else 0
        self.conn.commit()
        return deleted

    def commit(self) -> None:
        self._write_guard()
        self.conn.commit()

    # -- meta -----------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str, *, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        if commit:
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
        stats = {
            "files": self.file_count(),
            "edges": self.edge_count(),
            "p95_degree": p95,
            "top_hubs": [{"path": p, "degree": d} for p, d in sorted_hubs[:10]],
        }
        stats.update(self.cross_file_coverage())
        return stats

    def cross_file_coverage(self) -> dict[str, int | float]:
        """Share of files with at least one co-change or structural edge."""
        total = self.file_count()
        if total == 0:
            return {"connected_files": 0, "cross_file_coverage_pct": 0.0}
        connected = 0
        if self._table_exists("structural_edges"):
            row = self.conn.execute(
                """SELECT COUNT(DISTINCT path) FROM (
                       SELECT src_path AS path FROM structural_edges
                       UNION SELECT dst_path AS path FROM structural_edges
                   )"""
            ).fetchone()
            connected = int(row[0]) if row and row[0] is not None else 0
        if self._table_exists("co_changes"):
            row = self.conn.execute(
                """SELECT COUNT(DISTINCT f.path)
                   FROM files f
                   JOIN co_changes c ON c.file_a = f.id OR c.file_b = f.id"""
            ).fetchone()
            co_count = int(row[0]) if row and row[0] is not None else 0
            connected = max(connected, co_count)
        pct = round(100.0 * connected / total, 1) if total else 0.0
        return {"connected_files": connected, "cross_file_coverage_pct": pct}

    def log_feedback(
        self, query: str, file_path: str, returned: bool = True, used: bool = False
    ) -> None:
        self._write_guard()
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

    def has_feedback_dedup(self, event_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM feedback_dedup WHERE event_key = ? LIMIT 1",
            (event_key,),
        ).fetchone()
        return row is not None

    def add_feedback_dedup(self, event_key: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO feedback_dedup(event_key, ts) VALUES (?, ?)",
            (event_key, int(time.time())),
        )

    def feedback_rows_by_file(self) -> list[tuple[str, int, int]]:
        return self.conn.execute(
            "SELECT file_path, SUM(used), COUNT(*) FROM feedback GROUP BY file_path"
        ).fetchall()

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

    def rebuild_files_fts(self) -> None:
        """Resync path FTS with the co-change files table."""
        if not self._has_fts:
            return
        self.conn.execute("DELETE FROM files_fts")
        rows = self.conn.execute("SELECT id, path FROM files").fetchall()
        for rowid, path in rows:
            self.conn.execute(
                "INSERT INTO files_fts(rowid, path) VALUES (?, ?)",
                (rowid, path),
            )

    def search_files(self, pattern: str, limit: int = 20) -> list[str]:
        """Search file paths by FTS5 (graph + indexed paths) or LIKE fallback."""
        fts_query = _fts_query(pattern)
        ranked: list[str] = []
        seen: set[str] = set()

        if self._has_fts:
            try:
                rows = self.conn.execute(
                    "SELECT path FROM files_fts WHERE files_fts MATCH ? LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
                for (path,) in rows:
                    if path not in seen:
                        ranked.append(path)
                        seen.add(path)
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                if not self.readonly:
                    self.rebuild_files_fts()
                    try:
                        rows = self.conn.execute(
                            "SELECT path FROM files_fts WHERE files_fts MATCH ? LIMIT ?",
                            (fts_query, limit),
                        ).fetchall()
                        for (path,) in rows:
                            if path not in seen:
                                ranked.append(path)
                                seen.add(path)
                    except (sqlite3.OperationalError, sqlite3.DatabaseError):
                        pass

        if self._has_search_paths_fts and len(ranked) < limit:
            try:
                rows = self.conn.execute(
                    "SELECT path FROM search_paths_fts WHERE search_paths_fts MATCH ? LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
                for (path,) in rows:
                    if path not in seen:
                        ranked.append(path)
                        seen.add(path)
            except sqlite3.OperationalError:
                pass

        if ranked:
            return ranked[:limit]

        rows = self.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? LIMIT ?",
            (f"%{pattern}%", limit),
        ).fetchall()
        return [r[0] for r in rows]

    def has_search_index(self) -> bool:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'search_index_version'"
        ).fetchone()
        return row is not None

    def clear_file_search_index(self, path: str) -> None:
        """Remove per-file symbol, content, path, and structural index rows."""
        file_id = self.file_id(path)
        if file_id is not None:
            self.conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        if self._has_symbols_fts:
            self.conn.execute("DELETE FROM symbols_fts WHERE path = ?", (path,))
        if self._has_content_fts:
            self.conn.execute("DELETE FROM content_fts WHERE path = ?", (path,))
        if self._has_search_paths_fts:
            self.conn.execute("DELETE FROM search_paths_fts WHERE path = ?", (path,))
        self.conn.execute("DELETE FROM structural_edges WHERE src_path = ?", (path,))
        if self._table_exists("index_state"):
            self.conn.execute("DELETE FROM index_state WHERE path = ?", (path,))

    def get_index_state(self, path: str) -> tuple[int, int] | None:
        if not self._table_exists("index_state"):
            return None
        row = self.conn.execute(
            "SELECT mtime_ns, size FROM index_state WHERE path = ?",
            (path,),
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else None

    def set_index_state(self, path: str, mtime_ns: int, size: int) -> None:
        if not self._table_exists("index_state"):
            return
        self.conn.execute(
            """INSERT INTO index_state(path, mtime_ns, size)
               VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET mtime_ns = excluded.mtime_ns, size = excluded.size""",
            (path, mtime_ns, size),
        )

    def clear_search_indexes(self) -> None:
        self.conn.execute("DELETE FROM symbols")
        if self._has_symbols_fts:
            self.conn.execute("DELETE FROM symbols_fts")
        if self._has_content_fts:
            self.conn.execute("DELETE FROM content_fts")
        if self._has_search_paths_fts:
            self.conn.execute("DELETE FROM search_paths_fts")
        self.conn.execute("DELETE FROM structural_edges")

    def add_structural_edge(
        self,
        src_path: str,
        dst_path: str,
        kind: str,
        confidence: str = "INFERRED",
    ) -> None:
        self.conn.execute(
            """INSERT INTO structural_edges(src_path, dst_path, kind, confidence)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(src_path, dst_path, kind) DO NOTHING""",
            (src_path, dst_path, kind, confidence),
        )

    def structural_neighbours(
        self,
        path: str,
        *,
        kinds: set[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[str, str, str]]:
        """Return (dst_path, kind, confidence) for structural edges from *path*."""
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            rows = self.conn.execute(
                f"""SELECT dst_path, kind, confidence
                    FROM structural_edges
                    WHERE src_path = ? AND kind IN ({placeholders})
                    LIMIT ?""",
                (path, *sorted(kinds), limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT dst_path, kind, confidence
                   FROM structural_edges
                   WHERE src_path = ?
                   LIMIT ?""",
                (path, limit),
            ).fetchall()
        return [(dst, kind, confidence) for dst, kind, confidence in rows]

    def structural_incoming(
        self,
        path: str,
        *,
        kinds: set[str] | None = None,
        limit: int = 50,
    ) -> list[tuple[str, str, str]]:
        """Return (src_path, kind, confidence) for edges pointing at *path*."""
        if not self._table_exists("structural_edges"):
            return []
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            rows = self.conn.execute(
                f"""SELECT src_path, kind, confidence
                    FROM structural_edges
                    WHERE dst_path = ? AND kind IN ({placeholders})
                    LIMIT ?""",
                (path, *sorted(kinds), limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT src_path, kind, confidence
                   FROM structural_edges
                   WHERE dst_path = ?
                   LIMIT ?""",
                (path, limit),
            ).fetchall()
        return [(src, kind, confidence) for src, kind, confidence in rows]

    def structural_edge_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM structural_edges").fetchone()
        return int(row[0]) if row else 0

    def index_search_path(self, path: str) -> None:
        if self._has_search_paths_fts:
            self.conn.execute("INSERT INTO search_paths_fts(path) VALUES (?)", (path,))

    def index_file_symbols(self, path: str, records: list[dict]) -> None:
        if not records:
            return
        file_id = self.file_id(path)
        for record in records:
            if file_id is not None:
                self.conn.execute(
                    """INSERT INTO symbols(file_id, symbol, kind, line, container)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        file_id,
                        record["symbol"],
                        record.get("kind", "symbol"),
                        int(record.get("line", 1)),
                        record.get("container", ""),
                    ),
                )
            if self._has_symbols_fts:
                self.conn.execute(
                    "INSERT INTO symbols_fts(symbol, kind, path, line) VALUES (?, ?, ?, ?)",
                    (
                        record["symbol"],
                        record.get("kind", "symbol"),
                        path,
                        int(record.get("line", 1)),
                    ),
                )

    def index_file_content(self, path: str, body: str) -> None:
        if not self._has_content_fts or not body.strip():
            return
        self.conn.execute(
            "INSERT INTO content_fts(path, body) VALUES (?, ?)",
            (path, body),
        )

    def search_symbols(self, query: str, limit: int = 20) -> list[tuple[str, float, str, int]]:
        """Search symbol definitions. Returns (path, score, symbol, line)."""
        if not self._has_symbols_fts:
            return self._search_symbols_like(query, limit)
        fts_query = _fts_query(query)
        try:
            rows = self.conn.execute(
                """SELECT path, symbol, line, bm25(symbols_fts) AS rank
                   FROM symbols_fts
                   WHERE symbols_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return self._search_symbols_like(query, limit)
        if not rows:
            return self._search_symbols_like(query, limit)
        return [(path, float(-rank), symbol, int(line)) for path, symbol, line, rank in rows]

    def _search_symbols_like(
        self, query: str, limit: int = 20
    ) -> list[tuple[str, float, str, int]]:
        terms = re.findall(r"[a-zA-Z]\w{2,}", query)
        if not terms:
            return []
        results: list[tuple[str, float, str, int]] = []
        for term in terms[:4]:
            pattern = f"%{term}%"
            rows = self.conn.execute(
                """SELECT f.path, s.symbol, s.line
                   FROM symbols s
                   JOIN files f ON f.id = s.file_id
                   WHERE s.symbol LIKE ?
                   ORDER BY LENGTH(s.symbol) ASC
                   LIMIT ?""",
                (pattern, limit),
            ).fetchall()
            for path, symbol, line in rows:
                score = 10.0 if symbol == term else 5.0
                results.append((path, score, symbol, int(line)))
        seen: set[tuple[str, str]] = set()
        deduped: list[tuple[str, float, str, int]] = []
        for item in sorted(results, key=lambda x: -x[1]):
            key = (item[0], item[2])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def search_content_bm25(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        """BM25 search over indexed file contents."""
        if not self._has_content_fts:
            return []
        fts_query = _fts_query(query)
        try:
            rows = self.conn.execute(
                """SELECT path, bm25(content_fts) AS rank
                   FROM content_fts
                   WHERE content_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(path, float(-rank)) for path, rank in rows]

    def clear_spec_index(self, path: str) -> None:
        if self._has_specs_fts:
            self.conn.execute("DELETE FROM specs_fts WHERE path = ?", (path,))
        if self._table_exists("spec_index_state"):
            self.conn.execute("DELETE FROM spec_index_state WHERE path = ?", (path,))

    def clear_all_spec_indexes(self) -> None:
        if self._has_specs_fts:
            self.conn.execute("DELETE FROM specs_fts")
        if self._table_exists("spec_index_state"):
            self.conn.execute("DELETE FROM spec_index_state")

    def get_spec_index_state(self, path: str) -> tuple[int, int] | None:
        if not self._table_exists("spec_index_state"):
            return None
        row = self.conn.execute(
            "SELECT mtime_ns, size FROM spec_index_state WHERE path = ?",
            (path,),
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else None

    def set_spec_index_state(self, path: str, mtime_ns: int, size: int) -> None:
        if not self._table_exists("spec_index_state"):
            return
        self.conn.execute(
            """INSERT INTO spec_index_state(path, mtime_ns, size)
               VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET mtime_ns = excluded.mtime_ns,
                                             size = excluded.size""",
            (path, mtime_ns, size),
        )

    def index_spec_document(
        self,
        path: str,
        *,
        kind: str,
        title: str,
        body: str,
    ) -> None:
        if not self._has_specs_fts or not body.strip():
            return
        self.conn.execute(
            "INSERT INTO specs_fts(path, kind, title, body) VALUES (?, ?, ?, ?)",
            (path, kind, title, body),
        )

    def search_specs_bm25(self, query: str, limit: int = 10) -> list[tuple[str, float, str, str]]:
        """Return (path, score, kind, title) for codified context documents."""
        if not self._has_specs_fts:
            return []
        fts_query = _fts_query(query)
        try:
            rows = self.conn.execute(
                """SELECT path, kind, title, bm25(specs_fts) AS rank
                   FROM specs_fts
                   WHERE specs_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(path, float(-rank), kind, title) for path, kind, title, rank in rows]

    def unified_search(self, query: str, limit: int = 20) -> dict:
        """Path + symbol + BM25 search for the `search` command."""
        paths = self.search_files(query, limit=limit)
        symbols = self.search_symbols(query, limit=limit)
        content = self.search_content_bm25(query, limit=limit)
        specs = self.search_specs_bm25(query, limit=limit)

        ranked_paths: list[str] = []
        seen: set[str] = set()
        for path in paths:
            if path not in seen:
                ranked_paths.append(path)
                seen.add(path)
        for path, _score, _symbol, _line in symbols:
            if path not in seen:
                ranked_paths.append(path)
                seen.add(path)
        for path, _score in content:
            if path not in seen:
                ranked_paths.append(path)
                seen.add(path)

        symbol_hits = [
            {"path": path, "symbol": symbol, "line": line, "score": score}
            for path, score, symbol, line in symbols[:limit]
        ]
        return {
            "files": ranked_paths[:limit],
            "symbols": symbol_hits,
            "content_hits": [{"path": path, "score": score} for path, score in content[:limit]],
            "spec_hits": [
                {"path": path, "score": score, "kind": kind, "title": title}
                for path, score, kind, title in specs[:limit]
            ],
            "count": len(ranked_paths[:limit]),
        }

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
            if (
                total_files == 0
                or largest <= max(total_files * 0.3, max_community_size)
                or threshold >= 10
            ):
                communities.sort(key=len, reverse=True)
                return communities
            threshold += 1
