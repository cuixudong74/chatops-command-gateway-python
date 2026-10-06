"""Domain models: statuses, command records, audit events, callback envelopes."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


class CommandStatus(str, enum.Enum):
    RECEIVED = "received"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EventType(str, enum.Enum):
    COMMAND_MESSAGE = "command_message"
    BUTTON_INTERACTION = "button_interaction"
    MESSAGE_ACTION = "message_action"


@dataclass(frozen=True)
class CallbackEnvelope:
    """A verified inbound callback from the collaboration platform."""

    delivery_id: str          # platform-unique id of this delivery (idempotency key)
    event_type: EventType
    workspace_id: str
    actor_id: str
    command_id: str           # platform-unique id of the logical command
    action: str               # e.g. "deploy", "approve", "reject", "run"
    args: dict[str, Any] = field(default_factory=dict)
    occurred_at: float = 0.0  # platform-side timestamp (epoch seconds)


@dataclass
class CommandRecord:
    command_id: str
    workspace_id: str
    action: str
    args: dict[str, Any]
    status: CommandStatus
    created_by: str
    created_at: float
    updated_at: float
    approved_by: Optional[str] = None
    rejected_by: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    version: int = 0          # optimistic-concurrency version


@dataclass(frozen=True)
class AuditEvent:
    seq: int                  # monotonically increasing, assigned by store
    ts: float
    command_id: str
    delivery_id: str
    actor_id: str
    event: str                # e.g. "received", "transition", "rejected_request"
    detail: dict[str, Any] = field(default_factory=dict)
