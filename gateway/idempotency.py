"""Callback idempotency and out-of-order buffering.

Every platform event carries a unique ``event_id``. Recording it is an atomic
INSERT: the first delivery wins, every redelivery is reported as a duplicate
and never produces side effects.

Approve/reject interactions sometimes arrive before the command message
(platform retries, out-of-order delivery). Such interactions are buffered
(also keyed by a unique event_id) and replayed once the command exists.
"""
from __future__ import annotations

import json
import time

from gateway.db import Database
from gateway.models import CallbackPayload

# Events older than this are not buffered as "early"; they are just rejected
# as referencing an unknown command.
BUFFER_TTL_MS = 24 * 3600 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


class IdempotencyStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def first_seen(self, event_id: str, payload: CallbackPayload) -> bool:
        """Atomically record an event. True if first time, False duplicate."""
        try:
            self.db.execute(
                "INSERT INTO processed_events(event_id, command_id, "
                "payload_json, seen_at) VALUES (?, ?, ?, ?)",
                (
                    event_id,
                    payload.command_id,
                    payload.model_dump_json(),
                    _now_ms(),
                ),
            )
            return True
        except Exception as exc:  # sqlite3.IntegrityError on duplicate PK
            if getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_PRIMARYKEY":
                return False
            raise

    def is_duplicate(self, event_id: str) -> bool:
        return (
            self.db.query_one(
                "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
            )
            is not None
        )

    def buffer_interaction(self, payload: CallbackPayload) -> bool:
        """Store an early interaction. False if it was already buffered."""
        try:
            self.db.execute(
                "INSERT INTO pending_interactions(command_id, event_id, "
                "payload_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    payload.command_id,
                    payload.event_id,
                    payload.model_dump_json(),
                    _now_ms(),
                ),
            )
            return True
        except Exception as exc:
            if getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_UNIQUE":
                return False
            raise

    def take_buffered(self, command_id: str) -> list[CallbackPayload]:
        rows = self.db.query_all(
            "SELECT id, payload_json FROM pending_interactions "
            "WHERE command_id = ? ORDER BY id",
            (command_id,),
        )
        result: list[CallbackPayload] = []
        ids: list[int] = []
        for row in rows:
            ids.append(row["id"])
            result.append(CallbackPayload.model_validate_json(row["payload_json"]))
        for row_id in ids:
            self.db.execute(
                "DELETE FROM pending_interactions WHERE id = ?", (row_id,)
            )
        return result
