"""Core orchestration: dedupe, authorize, rate-limit, state machine, execute."""
from __future__ import annotations

import time
from typing import Any, Callable

from . import authz
from .config import Settings
from .errors import IllegalTransition, RateLimited
from .executor import Executor
from .models import (
    AuditEvent,
    CallbackEnvelope,
    CommandRecord,
    CommandStatus,
    EventType,
)
from .ratelimit import RateLimiter
from .storage import Store


class GatewayService:
    def __init__(self, settings: Settings, store: Store,
                 executor: Executor, limiter: RateLimiter,
                 clock: Callable[[], float] = time.time):
        self._settings = settings
        self._store = store
        self._executor = executor
        self._limiter = limiter
        self._clock = clock

    # ------------------------------------------------------------------
    # public entry points
    # ------------------------------------------------------------------
    def handle_callback(self, env: CallbackEnvelope) -> dict[str, Any]:
        """Process one verified callback envelope idempotently.

        The same delivery_id arriving any number of times produces the
        side effects of the first arrival only; repeats are reported as
        duplicates with the command's current state.
        """
        now = self._clock()
        if not self._store.claim_delivery(env.delivery_id, env.command_id, now):
            self._audit(env, "duplicate_delivery", {})
            return self._response(env.command_id, duplicate=True)
        self._audit(env, "delivery_received",
                    {"event_type": env.event_type.value, "action": env.action})

        if not self._limiter.check(env.workspace_id, env.actor_id, now):
            raise RateLimited(
                f"rate limit exceeded for {env.actor_id} in {env.workspace_id}",
                detail={"workspace_id": env.workspace_id,
                        "actor_id": env.actor_id},
            )

        if env.event_type == EventType.COMMAND_MESSAGE:
            return self._handle_new_command(env, now)
        return self._handle_interaction(env, now)

    def recover(self) -> list[CommandRecord]:
        """Resume unfinished commands after a restart.

        - received/submitted  -> driven forward to pending_approval
        - pending_approval    -> left waiting for a human decision
        - approved/executing  -> re-executed (executor must be idempotent
                                 per command_id, so a crash mid-execution
                                 never causes a double side effect)
        Terminal commands are never touched.
        """
        resumed: list[CommandRecord] = []
        for rec in self._store.list_resumable():
            try:
                if rec.status in (CommandStatus.RECEIVED, CommandStatus.SUBMITTED):
                    resumed.append(self._advance_to_pending(rec))
                elif rec.status in (CommandStatus.APPROVED, CommandStatus.EXECUTING):
                    resumed.append(self._execute(rec.command_id, rec.approved_by or "system"))
                else:
                    resumed.append(rec)
            except IllegalTransition:
                # Another worker already advanced it; that is fine.
                current = self._store.get_command(rec.command_id)
                if current:
                    resumed.append(current)
        return resumed

    # ------------------------------------------------------------------
    # command lifecycle
    # ------------------------------------------------------------------
    def _handle_new_command(self, env: CallbackEnvelope, now: float) -> dict:
        authz.check_submit(self._settings, env.workspace_id, env.actor_id)
        existing = self._store.get_command(env.command_id)
        if existing is not None:
            # Same logical command re-delivered under a new delivery id
            # (platform retry with a fresh delivery id, or out-of-order
            # arrival). Idempotent: no new record, no new side effects.
            self._audit(env, "duplicate_command",
                        {"status": existing.status.value})
            return self._response(env.command_id, duplicate=True)
        record = CommandRecord(
            command_id=env.command_id,
            workspace_id=env.workspace_id,
            action=env.action,
            args=env.args,
            status=CommandStatus.RECEIVED,
            created_by=env.actor_id,
            created_at=now,
            updated_at=now,
        )
        self._store.insert_command(record)
        self._audit(env, "command_created", {"action": env.action})
        self._advance_to_pending(record, env)
        return self._response(env.command_id, duplicate=False)

    def _advance_to_pending(self, rec: CommandRecord,
                            env: CallbackEnvelope | None = None) -> CommandRecord:
        for src, dst in ((CommandStatus.RECEIVED, CommandStatus.SUBMITTED),
                         (CommandStatus.SUBMITTED, CommandStatus.PENDING_APPROVAL)):
            if rec.status == src:
                rec = self._store.transition(rec.command_id, src, dst, self._clock())
                self._audit_transition(rec, src, dst, env)
        return rec

    def _handle_interaction(self, env: CallbackEnvelope, now: float) -> dict:
        authz.check_approve(self._settings, env.workspace_id, env.actor_id)
        if env.action == "approve":
            return self._decide(env, CommandStatus.APPROVED, "approved_by")
        if env.action == "reject":
            return self._decide(env, CommandStatus.REJECTED, "rejected_by")
        # Non-approval message actions on the command (e.g. "details") are
        # acknowledged without state changes.
        self._audit(env, "interaction_ignored", {"action": env.action})
        return self._response(env.command_id, duplicate=False)

    def _decide(self, env: CallbackEnvelope, dst: CommandStatus,
                field: str) -> dict:
        try:
            rec = self._store.transition(
                env.command_id, CommandStatus.PENDING_APPROVAL, dst,
                self._clock(), **{field: env.actor_id})
        except IllegalTransition:
            # Lost the race against a concurrent approver, or the command
            # was already decided: report current state, do not error out.
            self._audit(env, "decision_conflict",
                        {"attempted": dst.value})
            return self._response(env.command_id, duplicate=True)
        self._audit_transition(rec, CommandStatus.PENDING_APPROVAL, dst, env)
        if dst == CommandStatus.APPROVED:
            self._execute(rec.command_id, env.actor_id, env)
        return self._response(env.command_id, duplicate=False)

    def _execute(self, command_id: str, actor: str,
                 env: CallbackEnvelope | None = None) -> CommandRecord:
        rec = self._store.get_command(command_id)
        assert rec is not None
        if rec.status == CommandStatus.APPROVED:
            rec = self._store.transition(command_id, CommandStatus.APPROVED,
                                         CommandStatus.EXECUTING, self._clock())
            self._audit_transition(rec, CommandStatus.APPROVED,
                                   CommandStatus.EXECUTING, env, actor)
        # Status is EXECUTING and durably persisted before the side effect
        # runs; a crash here is recovered by re-executing idempotently.
        try:
            result = self._executor.execute(rec)
        except Exception as exc:  # noqa: BLE001 - any executor failure -> FAILED
            rec = self._store.transition(command_id, CommandStatus.EXECUTING,
                                         CommandStatus.FAILED, self._clock(),
                                         result={"error": str(exc)})
            self._audit_transition(rec, CommandStatus.EXECUTING,
                                   CommandStatus.FAILED, env, actor)
            return rec
        rec = self._store.transition(command_id, CommandStatus.EXECUTING,
                                     CommandStatus.SUCCEEDED, self._clock(),
                                     result=result)
        self._audit_transition(rec, CommandStatus.EXECUTING,
                               CommandStatus.SUCCEEDED, env, actor)
        return rec

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _response(self, command_id: str, duplicate: bool) -> dict[str, Any]:
        rec = self._store.get_command(command_id)
        return {
            "command_id": command_id,
            "status": rec.status.value if rec else "unknown",
            "duplicate": duplicate,
            "result": rec.result if rec else None,
        }

    def _audit(self, env: CallbackEnvelope, event: str, detail: dict) -> None:
        self._store.append_audit(AuditEvent(
            seq=0, ts=self._clock(), command_id=env.command_id,
            delivery_id=env.delivery_id, actor_id=env.actor_id,
            event=event, detail=detail))

    def _audit_transition(self, rec: CommandRecord, src: CommandStatus,
                          dst: CommandStatus, env: CallbackEnvelope | None,
                          actor: str | None = None) -> None:
        self._store.append_audit(AuditEvent(
            seq=0, ts=self._clock(), command_id=rec.command_id,
            delivery_id=env.delivery_id if env else "",
            actor_id=(env.actor_id if env else None) or actor or "system",
            event="transition",
            detail={"from": src.value, "to": dst.value}))
