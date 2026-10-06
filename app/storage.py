"""SQLite-backed durable store: commands, idempotent deliveries, append-only audit."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Optional

from .errors import IllegalTransition
from .models import AuditEvent, CommandRecord, CommandStatus
from .statemachine import can_transition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    command_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    action       TEXT NOT NULL,
    args         TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    approved_by  TEXT,
    rejected_by  TEXT,
    result       TEXT,
    version      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    command_id  TEXT NOT NULL,
    first_seen  REAL NOT NULL
);
-- Append-only audit log. seq is assigned by SQLite (monotonic per db).
CREATE TABLE IF NOT EXISTS audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    command_id  TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    event       TEXT NOT NULL,
    detail      TEXT NOT NULL
);
-- Tamper resistance: forbid UPDATE/DELETE on the audit log at the db layer.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class Store:
    """All mutations go through short transactions on a single connection.

    A single connection guarded by an RLock gives us serialized writers,
    which is what makes the compare-and-swap transitions race-free.
    """

    def __init__(self, db_path: str = ":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # --- idempotency ---
    def claim_delivery(self, delivery_id: str, command_id: str, ts: float) -> bool:
        """Atomically claim a delivery id. True = first time seen."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO deliveries(delivery_id, command_id, first_seen)"
                " VALUES (?, ?, ?)",
                (delivery_id, command_id, ts),
            )
            return cur.rowcount == 1

    # --- commands ---
    def insert_command(self, record: CommandRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO commands(command_id, workspace_id, action, args, status,"
                " created_by, created_at, updated_at, approved_by, rejected_by,"
                " result, version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.command_id, record.workspace_id, record.action,
                 json.dumps(record.args), record.status.value, record.created_by,
                 record.created_at, record.updated_at, record.approved_by,
                 record.rejected_by,
                 json.dumps(record.result) if record.result is not None else None,
                 record.version),
            )

    def get_command(self, command_id: str) -> Optional[CommandRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return self._to_record(row) if row else None

    def transition(self, command_id: str, expect: CommandStatus,
                   dst: CommandStatus, ts: float, **fields: Any) -> CommandRecord:
        """Compare-and-swap status; raises IllegalTransition on any mismatch."""
        if not can_transition(expect, dst):
            raise IllegalTransition(
                f"{expect.value} -> {dst.value} is not a legal transition",
                detail={"command_id": command_id, "from": expect.value, "to": dst.value},
            )
        sets = ["status = ?", "updated_at = ?", "version = version + 1"]
        params: list[Any] = [dst.value, ts]
        for key, value in fields.items():
            if key not in ("approved_by", "rejected_by", "result"):
                raise ValueError(f"cannot set field {key!r} via transition")
            sets.append(f"{key} = ?")
            params.append(json.dumps(value) if key == "result" else value)
        params += [command_id, expect.value]
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE commands SET {', '.join(sets)}"
                " WHERE command_id = ? AND status = ?",
                params,
            )
            if cur.rowcount != 1:
                actual = self.get_command(command_id)
                raise IllegalTransition(
                    f"command {command_id} is not in state {expect.value}",
                    detail={
                        "command_id": command_id,
                        "expected": expect.value,
                        "actual": actual.status.value if actual else None,
                    },
                )
        return self.get_command(command_id)  # type: ignore[return-value]

    def list_resumable(self) -> list[CommandRecord]:
        """Commands in non-terminal states needing recovery after restart."""
        terminal = ("rejected", "succeeded", "failed")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM commands WHERE status NOT IN ({','.join('?' * len(terminal))})"
                " ORDER BY created_at",
                terminal,
            ).fetchall()
        return [self._to_record(r) for r in rows]

    # --- audit (append-only) ---
    def append_audit(self, event: AuditEvent) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO audit(ts, command_id, delivery_id, actor_id, event, detail)"
                " VALUES (?,?,?,?,?,?)",
                (event.ts, event.command_id, event.delivery_id, event.actor_id,
                 event.event, json.dumps(event.detail)),
            )
            return int(cur.lastrowid)

    def list_audit(self, command_id: str | None = None) -> list[AuditEvent]:
        with self._lock:
            if command_id is None:
                rows = self._conn.execute("SELECT * FROM audit ORDER BY seq").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE command_id = ? ORDER BY seq",
                    (command_id,),
                ).fetchall()
        return [
            AuditEvent(seq=r["seq"], ts=r["ts"], command_id=r["command_id"],
                       delivery_id=r["delivery_id"], actor_id=r["actor_id"],
                       event=r["event"], detail=json.loads(r["detail"]))
            for r in rows
        ]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            command_id=row["command_id"],
            workspace_id=row["workspace_id"],
            action=row["action"],
            args=json.loads(row["args"]),
            status=CommandStatus(row["status"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approved_by=row["approved_by"],
            rejected_by=row["rejected_by"],
            result=json.loads(row["result"]) if row["result"] else None,
            version=row["version"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
