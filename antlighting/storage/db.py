"""Storage for collected server configurations (SQLite).

The database is intentionally small and boring: one table for configs, one for
test results, one for sources.  It lives in the per-user AntLighting data
directory and is safe to delete.

Secrets (share-link credentials) live in the ``raw`` column, which is required
to actually dial the server.  Nothing is uploaded anywhere and the log redactor
in :mod:`antlighting.logging_setup` keeps them out of log files.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Iterable

from ..configs.models import ServerConfig

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configs (
    identity        TEXT PRIMARY KEY,
    raw             TEXT NOT NULL,
    scheme          TEXT NOT NULL,
    address         TEXT NOT NULL,
    port            INTEGER NOT NULL,
    net             TEXT NOT NULL DEFAULT 'raw',
    security        TEXT NOT NULL DEFAULT 'none',
    credential      TEXT NOT NULL DEFAULT '',
    params          TEXT NOT NULL DEFAULT '{}',
    remark          TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    country         TEXT,
    country_code    TEXT,
    provider        TEXT,
    core_supported  INTEGER NOT NULL DEFAULT 1,
    unusable_reason TEXT NOT NULL DEFAULT '',
    source_id       TEXT NOT NULL DEFAULT '',
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    seen_count      INTEGER NOT NULL DEFAULT 1,
    last_tested     REAL,
    latency_ms      REAL,
    success_count   INTEGER NOT NULL DEFAULT 0,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_connected  REAL,
    status          TEXT NOT NULL DEFAULT 'unknown',
    last_error      TEXT NOT NULL DEFAULT '',
    trusted         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_configs_status ON configs (status);
CREATE INDEX IF NOT EXISTS idx_configs_scheme ON configs (scheme);
CREATE INDEX IF NOT EXISTS idx_configs_latency ON configs (latency_ms);

CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_fetch  REAL,
    last_count  INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT NOT NULL DEFAULT '',
    fetch_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS test_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started    REAL NOT NULL,
    finished   REAL,
    tested     INTEGER NOT NULL DEFAULT 0,
    working    INTEGER NOT NULL DEFAULT 0,
    trigger    TEXT NOT NULL DEFAULT 'manual'
);
"""


class ConfigStore:
    """Thread-safe SQLite wrapper for the local configuration pool."""

    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._initialise()

    # ------------------------------------------------------------------ schema
    def _initialise(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    def __enter__(self) -> ConfigStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ upsert
    def upsert_many(self, configs: Iterable[ServerConfig], now: float | None = None) -> int:
        """Insert new configs and refresh bookkeeping for known ones.

        Returns the number of *newly added* configs.
        """
        now = now if now is not None else time.time()
        added = 0
        rows = [c.to_dict() for c in configs]
        with self._lock:
            for row in rows:
                cur = self._conn.execute(
                    "SELECT 1 FROM configs WHERE identity = ?", (row["identity"],)
                )
                exists = cur.fetchone() is not None
                if exists:
                    self._conn.execute(
                        """
                        UPDATE configs SET last_seen = ?, seen_count = seen_count + 1,
                                           source_id = COALESCE(NULLIF(?, ''), source_id),
                                           name = COALESCE(NULLIF(?, ''), name),
                                           country = COALESCE(?, country),
                                           country_code = COALESCE(?, country_code),
                                           provider = COALESCE(?, provider)
                        WHERE identity = ?
                        """,
                        (
                            now,
                            row["source_id"],
                            row["name"],
                            row["country"],
                            row["country_code"],
                            row["provider"],
                            row["identity"],
                        ),
                    )
                else:
                    self._conn.execute(
                        """
                        INSERT INTO configs (identity, raw, scheme, address, port, net,
                            security, credential, params, remark, name, country,
                            country_code, provider, core_supported, unusable_reason,
                            source_id, first_seen, last_seen, seen_count, status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'unknown')
                        """,
                        (
                            row["identity"],
                            row["raw"],
                            row["scheme"],
                            row["address"],
                            row["port"],
                            row["net"],
                            row["security"],
                            row["credential"],
                            json.dumps(row["params"], ensure_ascii=False),
                            row["remark"],
                            row["name"],
                            row["country"],
                            row["country_code"],
                            row["provider"],
                            1 if row["core_supported"] else 0,
                            row["unusable_reason"],
                            row["source_id"],
                            now,
                            now,
                        ),
                    )
                    added += 1
            self._conn.commit()
        return added

    # ------------------------------------------------------------------ queries
    def _row_to_config(self, row: sqlite3.Row) -> ServerConfig:
        data = {key: row[key] for key in row.keys()}
        try:
            data["params"] = json.loads(data.get("params") or "{}")
        except (json.JSONDecodeError, TypeError):
            data["params"] = {}
        data["core_supported"] = bool(data.get("core_supported"))
        data["port"] = int(data.get("port") or 0)
        return ServerConfig.from_dict(data)

    def all(self, include_unusable: bool = True) -> list[ServerConfig]:
        query = "SELECT * FROM configs"
        if not include_unusable:
            query += " WHERE core_supported = 1 AND unusable_reason = ''"
        query += " ORDER BY first_seen"
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return [self._row_to_config(r) for r in rows]

    def get(self, identity: str) -> ServerConfig | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM configs WHERE identity = ?", (identity,)
            ).fetchone()
        return self._row_to_config(row) if row else None

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0])

    def count_usable(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM configs WHERE core_supported = 1 AND unusable_reason = ''"
            ).fetchone()
        return int(row[0])

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0]
            usable = self._conn.execute(
                "SELECT COUNT(*) FROM configs WHERE core_supported=1 AND unusable_reason=''"
            ).fetchone()[0]
            working = self._conn.execute(
                "SELECT COUNT(*) FROM configs WHERE status='working'"
            ).fetchone()[0]
            failed = self._conn.execute(
                "SELECT COUNT(*) FROM configs WHERE status IN ('failed','timeout')"
            ).fetchone()[0]
            untested = self._conn.execute(
                "SELECT COUNT(*) FROM configs WHERE status='unknown'"
            ).fetchone()[0]
            by_scheme = {
                r[0]: r[1]
                for r in self._conn.execute(
                    "SELECT scheme, COUNT(*) FROM configs GROUP BY scheme ORDER BY 2 DESC"
                ).fetchall()
            }
            by_country = {
                (r[0] or "?"): r[1]
                for r in self._conn.execute(
                    "SELECT country, COUNT(*) FROM configs GROUP BY country ORDER BY 2 DESC LIMIT 15"
                ).fetchall()
            }
        return {
            "total": int(total),
            "usable": int(usable),
            "working": int(working),
            "failed": int(failed),
            "untested": int(untested),
            "by_scheme": by_scheme,
            "by_country": by_country,
        }

    # ------------------------------------------------------------------ results
    def record_result(
        self,
        identity: str,
        status: str,
        latency_ms: float | None = None,
        error: str = "",
        now: float | None = None,
    ) -> None:
        now = now if now is not None else time.time()
        success = 1 if status == "working" else 0
        with self._lock:
            self._conn.execute(
                """
                UPDATE configs SET
                    last_tested = ?,
                    status = ?,
                    latency_ms = ?,
                    last_error = ?,
                    success_count = success_count + ?,
                    failure_count = failure_count + (1 - ?),
                    consecutive_failures = CASE WHEN ? = 1 THEN 0 ELSE consecutive_failures + 1 END
                WHERE identity = ?
                """,
                (now, status, latency_ms, error, success, success, success, identity),
            )
            self._conn.commit()

    def record_connected(self, identity: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE configs SET last_connected = ? WHERE identity = ?", (now, identity)
            )
            self._conn.commit()

    def mark_trusted(self, identity: str, trusted: bool = True) -> None:
        """Flag a config as user-supplied rather than publicly collected."""
        with self._lock:
            self._conn.execute(
                "UPDATE configs SET trusted = ? WHERE identity = ?",
                (1 if trusted else 0, identity),
            )
            self._conn.commit()

    def get_metrics(self, identity: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """SELECT latency_ms, success_count, failure_count, consecutive_failures,
                          last_tested, status, last_error
                   FROM configs WHERE identity = ?""",
                (identity,),
            ).fetchone()
        if not row:
            return {}
        return dict(row)

    def prune(self, keep: int = 4000) -> int:
        """Drop the least useful rows so the database cannot grow forever."""
        with self._lock:
            count = int(self._conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0])
            if count <= keep:
                return 0
            self._conn.execute(
                """
                DELETE FROM configs WHERE identity IN (
                    SELECT identity FROM configs
                    ORDER BY (success_count * 2 - failure_count) ASC,
                             COALESCE(latency_ms, 1e9) DESC, last_seen ASC
                    LIMIT ?
                )
                """,
                (count - keep,),
            )
            self._conn.commit()
            return count - keep

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM configs")
            self._conn.commit()

    # ------------------------------------------------------------------ sources
    def upsert_source(
        self,
        source_id: str,
        kind: str,
        url: str = "",
        label: str = "",
        enabled: bool = True,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sources (id, kind, url, label, enabled)
                VALUES (?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    url = COALESCE(NULLIF(excluded.url, ''), sources.url),
                    label = COALESCE(NULLIF(excluded.label, ''), sources.label),
                    enabled = excluded.enabled
                """,
                (source_id, kind, url, label, 1 if enabled else 0),
            )
            self._conn.commit()

    def record_source_result(
        self, source_id: str, count: int, error: str = "", now: float | None = None
    ) -> None:
        """Record a fetch outcome, creating the source row if this is the first.

        Sources discovered at runtime (rather than seeded from settings) still
        have to be visible in the diagnostics screen, so the row is created here
        instead of requiring a prior :meth:`upsert_source`.
        """
        now = now if now is not None else time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sources (id, kind, url, label, enabled, last_fetch,
                                     last_count, last_error, fetch_count)
                VALUES (?, 'unknown', '', ?, 1, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    last_fetch = excluded.last_fetch,
                    last_count = excluded.last_count,
                    last_error = excluded.last_error,
                    fetch_count = sources.fetch_count + 1
                """,
                (source_id, source_id, now, count, error),
            )
            self._conn.commit()

    def set_source_enabled(self, source_id: str, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sources SET enabled = ? WHERE id = ?", (1 if enabled else 0, source_id)
            )
            self._conn.commit()

    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def begin_test_run(self, trigger: str = "manual", now: float | None = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO test_runs (started, trigger) VALUES (?,?)", (now, trigger)
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def end_test_run(self, run_id: int, tested: int, working: int, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE test_runs SET finished = ?, tested = ?, working = ? WHERE id = ?",
                (now, tested, working, run_id),
            )
            self._conn.commit()

    def last_test_run(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM test_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
