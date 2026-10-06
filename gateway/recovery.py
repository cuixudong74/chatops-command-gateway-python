"""Crash recovery.

On startup the service reconciles database state with what actually executed:

* Commands stuck in ``EXECUTING`` crashed while running (or while persisting
  their result). They are rolled back to ``APPROVED`` and re-executed; the
  handler's idempotency guarantees the side effect happens once.
* Commands in ``APPROVED`` never got an executor scheduled; they are
  resumed directly.
* ``PENDING_APPROVAL`` commands keep waiting for an approver.
* Terminal commands (``REJECTED`` / ``SUCCEEDED`` / ``FAILED``) are never
  touched again.

Every reconciliation writes an audit entry, so a restart is itself
traceable.
"""
from __future__ import annotations

from gateway.audit import AuditLog
from gateway.db import Database
from gateway.models import CommandStatus


def recover_pending(db: Database, audit: AuditLog) -> dict[str, list[str]]:
    """Reconcile stale state. Returns ids grouped by what must be resumed."""
    stuck_ids: list[str] = []
    resume_ids: list[str] = []
    db.execute("BEGIN IMMEDIATE")
    try:
        stuck = db.query_all(
            "SELECT command_id FROM commands WHERE status = ?",
            (CommandStatus.EXECUTING.value,),
        )
        stuck_ids = [r["command_id"] for r in stuck]
        for command_id in stuck_ids:
            db.execute(
                "UPDATE commands SET status = ?, version = version + 1 "
                "WHERE command_id = ?",
                (CommandStatus.APPROVED.value, command_id),
            )
            audit.append(
                command_id,
                "recovered_executing",
                actor="system",
                result="ok",
                detail={"rolled_back_to": CommandStatus.APPROVED.value},
            )

        approved = db.query_all(
            "SELECT command_id FROM commands WHERE status = ?",
            (CommandStatus.APPROVED.value,),
        )
        resume_ids = [r["command_id"] for r in approved]
        for command_id in resume_ids:
            audit.append(
                command_id,
                "recover_resume_execution",
                actor="system",
                result="ok",
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    return {"resume": resume_ids, "rolled_back": stuck_ids}
