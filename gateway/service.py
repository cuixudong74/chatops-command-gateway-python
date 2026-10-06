"""Core gateway service orchestrating the full command lifecycle.

Threading model: the HTTP layer runs callbacks on any worker thread; all
state-mutating database work for one command runs under a per-command key
lock AND inside a ``BEGIN IMMEDIATE`` transaction, so concurrent
approvals / retries are serialised and state is read-then-written
atomically. The (potentially slow) operational action itself runs in an
asyncio task without holding the DB lock, so one slow command never blocks
unrelated commands.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from gateway.actions import ActionResult, get_handler
from gateway.audit import AuditLog
from gateway.config import Settings
from gateway.db import Database
from gateway.idempotency import IdempotencyStore
from gateway.models import CallbackPayload, CallbackType, CommandStatus
from gateway.permissions import Policy, can_approve, can_submit
from gateway.ratelimit import RateLimitConfig, RateLimiter
from gateway.recovery import recover_pending
from gateway.states import require_transition

logger = logging.getLogger("gateway")


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Transaction:
    """Commit on clean exit, roll back on exception."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def __enter__(self) -> "_Transaction":
        self.db.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.db.execute("COMMIT")
        else:
            self.db.execute("ROLLBACK")


class GatewayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)
        self.audit = AuditLog(self.db)
        self.idem = IdempotencyStore(self.db)
        self.rate_limiter = RateLimiter(
            self.db,
            RateLimitConfig(
                settings.default_rate_per_minute,
                settings.workspace_rate_per_minute,
            ),
        )
        self.policy = Policy(
            roles=settings.workspace_roles,
            approvers=settings.approvers,
            require_different_approver=settings.require_different_approver,
        )
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()
        self._executor_tasks: set[asyncio.Task] = set()
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------ locks
    def key_lock(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _tx(self):
        """Context manager for a write transaction.

        All writers also hold the process-wide ``db.lock`` so read-check-write
        sequences are atomic within this process. The explicit transaction
        boundaries (rather than the driver's implicit deferred one) make
        rollback on errors reliable.
        """
        return _Transaction(self.db)

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self.db.connect()
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        with self.db.lock:
            recovered = recover_pending(self.db, self.audit)
        for command_id in recovered["resume"]:
            self._spawn_execution(command_id)
        self._started = True
        logger.info(
            "gateway started; resumed=%d rolled_back=%d",
            len(recovered["resume"]),
            len(recovered["rolled_back"]),
        )

    async def shutdown(self) -> None:
        if not self._started:
            return
        tasks = list(self._executor_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._started = False
        self.db.close()

    # ---------------------------------------------------------- command repo
    def _get_command(self, command_id: str):
        return self.db.query_one(
            "SELECT * FROM commands WHERE command_id = ?", (command_id,)
        )

    def _transition(
        self, command_id: str, dst: CommandStatus, *, expect: CommandStatus
    ) -> bool:
        """Atomic conditional state transition.

        Returns False when the current row is no longer in ``expect``
        (another concurrent operation won the race).
        """
        cur = self.db.execute(
            "UPDATE commands SET status = ?, version = version + 1, "
            "updated_at = ? WHERE command_id = ? AND status = ?",
            (dst.value, _now_ms(), command_id, expect.value),
        )
        return cur.rowcount == 1

    def _set_result(
        self, command_id: str, dst: CommandStatus, result: ActionResult
    ) -> bool:
        cur = self.db.execute(
            "UPDATE commands SET status = ?, result_json = ?, "
            "version = version + 1, updated_at = ? "
            "WHERE command_id = ? AND status = ?",
            (
                dst.value,
                json.dumps({"success": result.success, "output": result.output}),
                _now_ms(),
                command_id,
                CommandStatus.EXECUTING.value,
            ),
        )
        return cur.rowcount == 1

    # -------------------------------------------------------------- dispatch
    async def handle_callback(self, payload: CallbackPayload) -> dict:
        """Process one verified platform callback.

        Returns a response dict that always distinguishes a *first*
        processing from a *duplicate hit*.
        """
        with self.db.lock, self._tx():
            if not self.idem.first_seen(payload.event_id, payload):
                existing = self.db.query_one(
                    "SELECT command_id FROM processed_events WHERE event_id = ?",
                    (payload.event_id,),
                )
                self.audit.append(
                    (existing["command_id"] if existing else None),
                    "duplicate_callback",
                    actor=payload.user_id,
                    result="ignored",
                    related_id=payload.event_id,
                )
                duplicate = True
                related = existing["command_id"] if existing else None
            else:
                duplicate = False
                related = None
        if duplicate:
            return {
                "status": "duplicate",
                "command_id": related,
                "event_id": payload.event_id,
                "message": "callback already processed; side effect not repeated",
            }

        if payload.type is CallbackType.COMMAND:
            return await self._handle_command(payload)
        return await self._handle_interaction(payload)

    async def _handle_command(self, payload: CallbackPayload) -> dict:
        command_name = payload.command_name or ""
        role = payload.role or ""
        # Permission and rate-limit checks happen before any state change.
        if not can_submit(
            self.policy, payload.workspace_id, role, command_name
        ):
            with self.db.lock, self._tx():
                self.audit.append(
                    None,
                    "command_denied",
                    actor=payload.user_id,
                    result="permission_denied",
                    related_id=payload.event_id,
                    detail={"workspace": payload.workspace_id, "role": role,
                            "command": command_name},
                )
            from gateway.errors import PermissionDenied

            raise PermissionDenied(
                f"role '{role}' may not run '{command_name}' in workspace "
                f"'{payload.workspace_id}'"
            )

        try:
            with self.db.lock, self._tx():
                self.rate_limiter.check_and_consume(
                    payload.workspace_id, payload.user_id, payload.timestamp
                )
        except Exception:
            with self.db.lock, self._tx():
                self.audit.append(
                    None,
                    "command_rate_limited",
                    actor=payload.user_id,
                    result="rate_limited",
                    related_id=payload.event_id,
                )
            raise

        command_id = payload.command_id
        assert command_id is not None

        replay_results = []
        existing_status: str | None = None
        buffered: list = []
        with self.db.lock, self.key_lock(f"cmd:{command_id}"), self._tx():
            row = self._get_command(command_id)
            if row is None:
                self.db.execute(
                    "INSERT INTO commands(command_id, workspace_id, "
                    "submitter_id, submitter_role, command_name, "
                    "args_json, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        command_id,
                        payload.workspace_id,
                        payload.user_id,
                        role,
                        command_name,
                        json.dumps(payload.args or {}),
                        CommandStatus.PENDING_APPROVAL.value,
                        _now_ms(),
                        _now_ms(),
                    ),
                )
                self.audit.append(
                    command_id,
                    "command_submitted",
                    actor=payload.user_id,
                    result="ok",
                    related_id=payload.event_id,
                    detail={"command": command_name, "args": payload.args},
                )
                # Replay approve/reject interactions that arrived first.
                buffered = self.idem.take_buffered(command_id)
            else:
                # Same command_id submitted again under a new event_id:
                # never create a second command / second side effect.
                buffered = []
                existing_status = row["status"]
                self.audit.append(
                    command_id,
                    "command_resubmit_ignored",
                    actor=payload.user_id,
                    result="ignored",
                    related_id=payload.event_id,
                    detail={"current_status": existing_status},
                )

        for early in buffered:
            # Re-dispatch synchronously on the same worker. The event was
            # already recorded in processed_events when first delivered, so
            # replaying it here is an internal redelivery and is simply
            # processed again (idempotent state machine prevents repeats).
            replay_results.append(await self._replay_buffered(early))

        if existing_status is not None:
            return {
                "status": "already_exists",
                "command_id": command_id,
                "command_status": existing_status,
                "event_id": payload.event_id,
                "message": "command_id already exists; not recreated",
            }

        # Buffered approvals may already have driven execution; read the
        # current status so the response reflects post-replay reality.
        final_status = self.get_command_status(command_id)
        return {
            "status": "accepted",
            "command_id": command_id,
            "command_status": (
                final_status["status"] if final_status
                else CommandStatus.PENDING_APPROVAL.value
            ),
            "event_id": payload.event_id,
            "buffered_interactions": len(buffered),
            "replay_results": replay_results,
        }

    async def _replay_buffered(self, payload: CallbackPayload) -> dict:
        # Bypass idempotency (already recorded); dispatch by type.
        if payload.type is CallbackType.COMMAND:
            return await self._handle_command(payload)
        return await self._handle_interaction(payload)

    async def _handle_interaction(self, payload: CallbackPayload) -> dict:
        command_id = payload.command_id
        assert command_id is not None
        action = (payload.action or "").lower()
        if action not in ("approve", "reject"):
            with self.db.lock:
                self.audit.append(
                    command_id,
                    "invalid_interaction",
                    actor=payload.user_id,
                    result="rejected",
                    related_id=payload.event_id,
                    detail={"action": action},
                )
            from gateway.errors import ApprovalError

            raise ApprovalError(f"unknown interaction action '{action}'")

        dst: CommandStatus | None = None
        response: dict | None = None
        with self.db.lock, self.key_lock(f"cmd:{command_id}"), self._tx():
            row = self._get_command(command_id)
            if row is None:
                # Out-of-order: interaction before command submission.
                self.idem.buffer_interaction(payload)
                self.audit.append(
                    command_id,
                    "interaction_buffered",
                    actor=payload.user_id,
                    result="buffered",
                    related_id=payload.event_id,
                    detail={"action": action},
                )
                response = {
                    "status": "buffered",
                    "command_id": command_id,
                    "event_id": payload.event_id,
                    "message": "interaction arrived before command; queued",
                }
            else:
                current = CommandStatus(row["status"])
                if current is not CommandStatus.PENDING_APPROVAL:
                    self.audit.append(
                        command_id,
                        f"{action}_ignored",
                        actor=payload.user_id,
                        result="ignored",
                        related_id=payload.event_id,
                        detail={"current_status": current.value},
                    )
                    response = {
                        "status": "ignored",
                        "command_id": command_id,
                        "command_status": current.value,
                        "event_id": payload.event_id,
                        "message": f"command already {current.value}",
                    }
                elif action == "approve" and not can_approve(
                    self.policy,
                    row["workspace_id"],
                    payload.user_id,
                    row["submitter_id"],
                ):
                    self.audit.append(
                        command_id,
                        "approve_denied",
                        actor=payload.user_id,
                        result="permission_denied",
                        related_id=payload.event_id,
                    )
                    from gateway.errors import PermissionDenied

                    raise PermissionDenied(
                        f"user '{payload.user_id}' is not allowed to "
                        f"approve command '{command_id}'"
                    )
                else:
                    dst = (
                        CommandStatus.APPROVED
                        if action == "approve"
                        else CommandStatus.REJECTED
                    )
                    require_transition(current, dst)
                    moved = self._transition(command_id, dst, expect=current)
                    if not moved:
                        # Lost a concurrent race; re-read and report.
                        latest = CommandStatus(
                            self._get_command(command_id)["status"]
                        )
                        response = {
                            "status": "ignored",
                            "command_id": command_id,
                            "command_status": latest.value,
                            "event_id": payload.event_id,
                        }
                        dst = None
                    else:
                        self.audit.append(
                            command_id,
                            f"command_{action}d",
                            actor=payload.user_id,
                            result="ok",
                            related_id=payload.event_id,
                        )

        if dst is CommandStatus.APPROVED:
            self._spawn_execution(command_id)

        if response is not None:
            return response
        return {
            "status": "ok",
            "command_id": command_id,
            "command_status": dst.value,
            "event_id": payload.event_id,
        }

    # -------------------------------------------------------------- executor
    def _track(self, task: asyncio.Task) -> asyncio.Task:
        self._executor_tasks.add(task)
        task.add_done_callback(self._executor_tasks.discard)
        return task

    def _spawn_execution(self, command_id: str) -> None:
        if self._loop is None:
            raise RuntimeError("service not started")
        self._loop.call_soon_threadsafe(
            lambda: self._track(
                asyncio.ensure_future(self._execute(command_id), loop=self._loop)
            )
        )

    async def _execute(self, command_id: str) -> None:
        """Drive an approved command to a terminal state exactly once.

        An *attempt* number is stamped when execution starts; only a result
        carrying the current attempt is allowed to finalise the command. If
        execution times out (outcome unknown) the state is returned to
        APPROVED for recovery to retry - never optimistically FAILED - so a
        side effect that actually happened cannot be marked failed. Handlers
        must be idempotent on ``command_id`` because retries are possible.
        """
        command_name = ""
        args: dict = {}
        attempt = 0
        with self.db.lock, self.key_lock(f"cmd:{command_id}"), self._tx() as tx:
            row = self._get_command(command_id)
            if row is None:
                return
            current = CommandStatus(row["status"])
            if current is not CommandStatus.APPROVED:
                # Another executor already picked it up (or it was
                # rejected) - never run twice.
                return
            self.db.execute(
                "UPDATE commands SET status = ?, version = version + 1, "
                "attempt = attempt + 1, updated_at = ? "
                "WHERE command_id = ?",
                (CommandStatus.EXECUTING.value, _now_ms(), command_id),
            )
            attempt = row["attempt"] + 1
            command_name = row["command_name"]
            args = json.loads(row["args_json"])
            self.audit.append(
                command_id,
                "execution_started",
                actor="system",
                result="ok",
                detail={"attempt": attempt},
            )

        handler = get_handler(command_name)

        def _run() -> ActionResult:
            if handler is None:
                return ActionResult(
                    success=False, output={"error": f"no handler for {command_name}"}
                )
            try:
                return handler(command_name, args, command_id)
            except Exception as exc:  # explicit failure -> FAILED
                logger.exception("action %s failed", command_name)
                return ActionResult(success=False, output={"error": str(exc)})

        timed_out = False
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=self.settings.action_timeout_seconds,
            )
        except asyncio.TimeoutError:
            timed_out = True
            result = ActionResult(
                success=False,
                output={"error": f"timeout after {self.settings.action_timeout_seconds}s"},
            )

        retry_due = False
        with self.db.lock, self.key_lock(f"cmd:{command_id}"), self._tx():
            row = self._get_command(command_id)
            current = CommandStatus(row["status"])

            if timed_out:
                # Outcome unknown. Only the still-running attempt itself
                # would find EXECUTING here; recovery may already have
                # rolled us back (APPROVED) and started a new attempt.
                if (
                    current is CommandStatus.EXECUTING
                    and row["attempt"] == attempt
                ):
                    self.db.execute(
                        "UPDATE commands SET status = ?, "
                        "version = version + 1, updated_at = ? "
                        "WHERE command_id = ?",
                        (CommandStatus.APPROVED.value, _now_ms(), command_id),
                    )
                    self.audit.append(
                        command_id,
                        "execution_timeout_retry",
                        actor="system",
                        result="retry",
                        detail={"attempt": attempt},
                    )
                    retry_due = True
            elif current is CommandStatus.EXECUTING and row["attempt"] == attempt:
                # Definite success / failure: only the current attempt may
                # finalise, so a stale, late result cannot overwrite state.
                dst = (
                    CommandStatus.SUCCEEDED
                    if result.success
                    else CommandStatus.FAILED
                )
                require_transition(current, dst)
                self._set_result(command_id, dst, result)
                self.audit.append(
                    command_id,
                    "execution_succeeded" if result.success else "execution_failed",
                    actor="system",
                    result="ok" if result.success else "failed",
                    detail={**result.output, "attempt": attempt},
                )
            else:
                logger.warning(
                    "execute finished but %s is %s (attempt %s != %s); "
                    "result discarded",
                    command_id,
                    current.value,
                    row["attempt"],
                    attempt,
                )

        if retry_due:
            self._spawn_execution(command_id)

    # ----------------------------------------------------------------- views
    def get_command_status(self, command_id: str) -> dict | None:
        row = self._get_command(command_id)
        if row is None:
            return None
        data = dict(row)
        if data.get("result_json"):
            data["result"] = json.loads(data.pop("result_json"))
        else:
            data.pop("result_json", None)
        return data
