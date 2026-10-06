"""Domain enums and payload models."""
from __future__ import annotations

import enum

from pydantic import BaseModel


class CommandStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CallbackType(str, enum.Enum):
    COMMAND = "command"
    BUTTON = "button"
    ACTION = "message_action"


class CallbackPayload(BaseModel):
    event_id: str
    type: CallbackType
    workspace_id: str
    user_id: str
    role: str = ""
    timestamp: int
    command_id: str | None = None
    command_name: str | None = None
    args: dict | None = None
    action: str | None = None  # approve / reject
    text: str | None = None
