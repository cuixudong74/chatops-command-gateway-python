"""SQLite persistence layer.

A single file database in WAL mode. All writes go through one process-wide
lock so state transitions can rely on atomic "read-check-write" sections
(``BEGIN IMMEDIATE``); reads are allowed concurrently.
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    command_id    TEXT PRIMARY KEY,
    workspace_id  TEXT NOT NULL,
    submitter_id  TEXT NOT NULL,
    submitter_role TEXT NOT NULL DEFAULT '',
    command_name  TEXT NOT NULL,
    args_json     TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL,
    version       INTEGER NOT NULL DEFAULT 0,
    attempt       INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    result_json   TEXT
);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id     TEXT PRIMARY KEY,
    command_id   TEXT,
    payload_json TEXT NOT NULL,
    seen_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id  TEXT NOT NULL,
    event_id    TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    command_id  TEXT,
    event       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    result      TEXT NOT NULL,
    related_id  TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS security_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    request_id  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS rate_counters (
    scope   TEXT NOT NULL,
    window  INTEGER NOT NULL,
    counter INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, window)
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            conn = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(SCHEMA)
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        assert self._conn is not None, "Database.connect() first"
        return self._conn.execute(sql, params)

    def query_one(
        self, sql: str, params: Iterable[Any] = ()
    ) -> sqlite3.Row | None:
        assert self._conn is not None, "Database.connect() first"
        return self._conn.execute(sql, params).fetchone()

    def query_all(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[sqlite3.Row]:
        assert self._conn is not None, "Database.connect() first"
        return list(self._conn.execute(sql, params).fetchall())
