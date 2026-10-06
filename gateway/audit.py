"""Append-only audit trail and security failure log.

Rows are insert-only: the module never exposes UPDATE/DELETE. Each entry
records timestamp, command id, event, actor, result, a related correlation
id and a free-form JSON detail blob. Security failures (bad signature,
expired timestamp, ...) land in a separate table keyed by request id so a
rejected request can always be located.
"""
from __future__ import annotations

import json
import time
from typing import Any

from gateway.db import Database


def _now() -> int:
    return int(time.time() * 1000)


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def append(
        self,
        command_id: str | None,
        event: str,
        actor: str,
        result: str,
        related_id: str | None = None,
        detail: dict[str, Any] | None = None,
        now: int | None = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO audit_log(ts, command_id, event, actor, result,
                                  related_id, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now if now is not None else _now(),
                command_id,
                event,
                actor,
                result,
                related_id,
                json.dumps(detail or {}, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)

    def append_security(
        self,
        request_id: str,
        reason: str,
        detail: dict[str, Any] | None = None,
        now: int | None = None,
    ) -> int:
        cur = self.db.execute(
            """
            INSERT INTO security_log(ts, request_id, reason, detail_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                now if now is not None else _now(),
                request_id,
                reason,
                json.dumps(detail or {}, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        data = dict(row)
        data["detail"] = json.loads(data.pop("detail_json"))
        return data

    def list_for_command(self, command_id: str) -> list[dict[str, Any]]:
        return [
            self._row_to_dict(r)
            for r in self.db.query_all(
                "SELECT * FROM audit_log WHERE command_id = ? ORDER BY id",
                (command_id,),
            )
        ]

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._row_to_dict(r)
            for r in self.db.query_all(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]

    def list_security(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._row_to_dict(r)
            for r in self.db.query_all(
                "SELECT * FROM security_log ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
